"""Writing step: back up, swap, record and verify (doc/architecture.md §13).

The swapped values are computed from the **backup** (the original values), not from the live
database. Running an interrupted correction again therefore writes the same values once more
and never swaps anything back.
"""

import gzip
import hashlib
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .plan import Period, effective_corrections
from .store import CORRECTIONS, Store, checked, energy_line
from .votes import Vote, vote_days

log = logging.getLogger("emon2influx.fix_orientation")

BATCH = 5000
HEADER = "# emon2influx fix_orientation backup"


@dataclass
class Result:
    period: Period
    points: int
    backup: Path
    verified: dict[str, int]


def backup_path(directory: Path, period: Period) -> Path:
    # No signature in the file name; the file itself holds it.
    key = hashlib.sha256(f"{period.signature}|{period.start}|{period.stop}".encode()).hexdigest()
    return directory / f"{period.first_day}_{period.last_day}_{key[:12]}.lp.gz"


def write_backup(store: Store, period: Period, path: Path, measurement: str) -> int:
    """Original values of the period as gzipped line protocol; reused when already complete."""
    if path.exists():
        return sum(1 for line in read_backup(path))
    partial = path.with_suffix(".partial")
    start, stop = period.bounds()
    count = 0
    with gzip.open(partial, "wt", encoding="utf-8") as file:
        file.write(
            f"{HEADER} signature={checked(period.signature)} from={period.start} "
            f"to={period.stop} created={datetime.now(UTC).isoformat()}\n"
        )
        for point in store.energy_points(period.signature, start, stop):
            file.write(energy_line(measurement, period.signature, point) + "\n")
            count += 1
    partial.rename(path)
    return count


def read_backup(path: Path) -> Iterator[str]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        for line in file:
            if line.strip() and not line.startswith("#"):
                yield line.rstrip("\n")


def swapped(line: str) -> str:
    """The same point with energy_consumed and energy_produced exchanged."""
    head, fields, stamp = line.split(" ")
    values = dict(f.split("=", 1) for f in fields.split(","))
    return (
        f"{head} energy_consumed={values['energy_produced']},"
        f"energy_produced={values['energy_consumed']} {stamp}"
    )


def write_lines(
    store: Store, lines: Iterator[str], *, sleep: Callable[[float], None] = time.sleep
) -> int:
    count = 0
    batch: list[str] = []
    for line in lines:
        batch.append(line)
        if len(batch) >= BATCH:
            _write_with_retry(store, batch, sleep)
            count += len(batch)
            batch = []
    if batch:
        _write_with_retry(store, batch, sleep)
        count += len(batch)
    return count


def _write_with_retry(store: Store, batch: list[str], sleep: Callable[[float], None]) -> None:
    for attempt in range(6):
        try:
            store.write(batch)
            return
        except Exception as error:  # network or server; the run can be repeated safely
            if attempt == 5:
                raise RuntimeError(
                    f"writing failed after retries ({error}); run again to "
                    "resume, the backup makes that safe"
                ) from None
            delay = 2**attempt
            log.warning("write failed (%s); retrying in %d s", error, delay)
            sleep(delay)


def record(store: Store, period: Period, kind: str, points: int) -> None:
    store.write(
        [
            f"{CORRECTIONS},signature={checked(period.signature)} "
            f'from="{period.start}",to="{period.stop}",kind="{kind}",points={points}i '
            f"{time.time_ns()}"
        ]
    )


def verify(store: Store, period: Period) -> dict[str, int]:
    """Votes per orientation over the period, after the correction."""
    start, stop = period.bounds()
    votes = vote_days(store.daily_stats(period.signature, start, stop))
    counts = {v.value: 0 for v in Vote}
    for vote in votes:
        counts[vote.vote.value] += 1
    return counts


def already_corrected(store: Store, period: Period) -> bool:
    start, stop = period.bounds()
    done = effective_corrections(store.corrections(period.signature))
    return any(c.start < stop and start < c.stop for c in done)


def apply_period(store: Store, period: Period, backups: Path, measurement: str) -> Result:
    backups.mkdir(parents=True, exist_ok=True)
    path = backup_path(backups, period)
    expected = write_backup(store, period, path, measurement)
    written = write_lines(store, (swapped(line) for line in read_backup(path)))
    if written != expected:
        raise RuntimeError(f"wrote {written} points, backup holds {expected}")
    record(store, period, "counters_swapped", written)
    return Result(period, written, path, verify(store, period))


def restore(store: Store, path: Path) -> tuple[Period, int]:
    """Writes the original values of a backup back and records the restore."""
    with gzip.open(path, "rt", encoding="utf-8") as file:
        header = file.readline()
    if not header.startswith(HEADER):
        raise ValueError(f"{path} is not a fix_orientation backup")
    meta = dict(part.split("=", 1) for part in header.split()[4:])
    period = Period(meta["signature"], meta["from"], meta["to"], "", "", 0, 0, 0.0)
    written = write_lines(store, read_backup(path))
    record(store, period, "restored", written)
    return period, written
