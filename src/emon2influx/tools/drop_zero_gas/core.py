"""Rewrites households without a gas meter without their `gas_delivered=0` (§13.1).

The old parser wrote `gas_delivered=0` when the telegram had no gas reading. A real gas meter
reports a rising reading, never 0, so a household whose gas was 0 over its whole lifetime has
no gas meter. InfluxDB cannot delete one field (2.7: "delete by field is not supported"), so
each day is backed up, deleted and written back without that field.

Per day: back up all points, delete them, write them back without the gas field, check. The
backup is the source for the rewrite, so an interrupted run can be started again; `restore`
writes the backups back as they were.
"""

import gzip
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from ..fix_orientation.store import CORRECTIONS, checked, day_chunks, day_start, local_day

FIELD = "gas_delivered"
_FIELD_VALUE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|[^,\s]+)')


class GasStore(Protocol):
    bucket: str

    def field_summary(
        self, field: str, start: datetime, stop: datetime
    ) -> dict[str, tuple[int, float]]: ...

    def point_lines(self, signature: str, start: datetime, stop: datetime) -> list[str]: ...

    def delete_points(self, signature: str, start: datetime, stop: datetime) -> None: ...

    def write(self, lines: list[str]) -> None: ...


@dataclass
class GasPlan:
    bucket: str
    since: str
    until: str
    created: str
    households: dict[str, int] = field(default_factory=dict)  # signature → gas points

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "GasPlan":
        return cls(**json.loads(path.read_text()))


def make_plan(store: GasStore, since: datetime, until: datetime, now: datetime) -> GasPlan:
    """Households whose gas was 0 in every point, up to now (so including the new parser's
    data: a gas meter that appeared later keeps the household out)."""
    summary = store.field_summary(FIELD, since, now)
    households = {
        sig: count
        for sig, (count, maximum) in sorted(summary.items())
        if count > 0 and maximum == 0
    }
    return GasPlan(
        store.bucket,
        since.isoformat(),
        until.isoformat(),
        datetime.now(UTC).isoformat(),
        households,
    )


def fields_of(line: str) -> tuple[str, list[tuple[str, str]], str]:
    """(measurement and tags, [(field, raw value)], timestamp); values may be quoted strings."""
    head, rest = line.split(" ", 1)
    body, stamp = rest.rsplit(" ", 1)
    return head, _FIELD_VALUE.findall(body), stamp


def without_gas(line: str) -> str | None:
    head, values, stamp = fields_of(line)
    kept = [(k, v) for k, v in values if k != FIELD]
    if not kept:
        return None  # a point with only gas: it goes entirely
    return f"{head} {','.join(f'{k}={v}' for k, v in kept)} {stamp}"


def gas_values(lines: list[str]) -> list[float]:
    return [float(v.rstrip("i")) for line in lines for k, v in fields_of(line)[1] if k == FIELD]


def household_dir(backups: Path, signature: str) -> Path:
    # No signature in the directory name; meta.json inside holds it.
    return backups / hashlib.sha256(signature.encode()).hexdigest()[:12]


class GasRewriter:
    def __init__(self, store: GasStore, backups: Path) -> None:
        self.store = store
        self.backups = backups

    def rewrite(self, signature: str, since: datetime, until: datetime) -> tuple[int, int]:
        """(days rewritten, gas points removed). Raises when any gas value is not 0."""
        sig = checked(signature)
        directory = household_dir(self.backups, sig)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "meta.json").write_text(
            json.dumps({"signature": sig, "since": since.isoformat(), "until": until.isoformat()})
        )
        done_file = directory / "done.txt"
        done = set(done_file.read_text().split()) if done_file.exists() else set()
        days = removed = 0
        for start, stop in day_chunks(since, until):
            key = local_day(start).isoformat()
            if key in done:
                continue
            backup = directory / f"{key}.lp.gz"
            if backup.exists():
                lines = _read(backup)
            else:
                lines = self.store.point_lines(sig, start, stop)
                if gas_values(lines):
                    _write(backup, lines)
            gas = gas_values(lines)
            if any(value != 0 for value in gas):
                raise RuntimeError(f"{key}: a gas value is not 0; this household has a gas meter")
            if gas:
                cleaned = [c for c in (without_gas(line) for line in lines) if c is not None]
                self.store.delete_points(sig, start, stop)
                for i in range(0, len(cleaned), 5000):
                    self.store.write(cleaned[i : i + 5000])
                after = self.store.point_lines(sig, start, stop)
                if gas_values(after) or len(after) != len(cleaned):
                    raise RuntimeError(
                        f"{key}: check failed after rewriting; run again to "
                        "resume (the backup is kept)"
                    )
                days += 1
                removed += len(gas)
            with done_file.open("a") as file:
                file.write(key + "\n")
        self._record(sig, since, until, "gas_zero_removed", removed)
        return days, removed

    def restore(self, directory: Path) -> tuple[str, int]:
        meta = json.loads((directory / "meta.json").read_text())
        sig = checked(meta["signature"])
        days = 0
        for backup in sorted(directory.glob("*.lp.gz")):
            day = date.fromisoformat(backup.name.split(".")[0])
            start, stop = next(day_chunks(*_day_bounds(day)))
            self.store.delete_points(sig, start, stop)
            lines = _read(backup)
            for i in range(0, len(lines), 5000):
                self.store.write(lines[i : i + 5000])
            days += 1
        (directory / "done.txt").unlink(missing_ok=True)
        self._record(
            sig,
            datetime.fromisoformat(meta["since"]),
            datetime.fromisoformat(meta["until"]),
            "gas_zero_restored",
            days,
        )
        return sig, days

    def _record(self, sig: str, since: datetime, until: datetime, kind: str, count: int) -> None:
        stamp = int(datetime.now(UTC).timestamp() * 1e9)
        self.store.write(
            [
                f'{CORRECTIONS},signature={sig} from="{since.isoformat()}",'
                f'to="{until.isoformat()}",kind="{kind}",points={count}i {stamp}'
            ]
        )


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    return day_start(day), day_start(day + timedelta(days=1))


def _read(path: Path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        return [line.rstrip("\n") for line in file if line.strip()]


def _write(path: Path, lines: list[str]) -> None:
    partial = path.with_suffix(".partial")
    with gzip.open(partial, "wt", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    partial.rename(path)
