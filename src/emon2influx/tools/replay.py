"""Replays logs of the old emon2influx through the new parser and compares (doc §10).

    python -m emon2influx.tools.replay logs/emon2influx.log [more logs...]

Every `MQTT:` line of the old log is processed as if it had just arrived (at the log time) and
compared with the `InfluxDB:` line the old parser wrote for it. Nothing is written anywhere.
Signatures in the report are masked.
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..diagnostics import ErrorReporter
from ..metrics import Metrics
from ..payload.registry import DecoderRegistry
from ..pipeline import Pipeline
from ..points import PointBuilder
from ..signature import mask
from ..telegram.parser import TelegramParser

_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \w+\s+(MQTT|InfluxDB): (.*)$")
_OLD = re.compile(r"^dsmr,signature=(\S+) (\S+)$")
_FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|[^,]+)')
OLD_FIELDS = ("power_consumed", "power_produced", "energy_consumed", "energy_produced")


@dataclass
class Kit:
    meter: str = "-"
    version: str = "-"
    outcomes: Counter[str] = field(default_factory=Counter)


def parse_fields(text: str) -> dict[str, str]:
    return {k: v for k, v in _FIELD.findall(text)}


def num(fields: dict[str, str], key: str) -> float | None:
    return float(fields[key]) if key in fields else None


def compare(old: dict[str, str], new_lines: list[str]) -> list[str]:
    """Outcome labels for one message."""
    if not new_lines:
        return ["new rejected, old wrote"]
    new: dict[str, str] = {}
    for line in new_lines:
        match = _OLD.match(line.rsplit(" ", 1)[0])
        if match:
            new.update(parse_fields(match.group(2)))
    outcomes: list[str] = []
    if new.get("crc_valid") == "false":
        outcomes.append("written with crc_valid=false")
    oc, op = num(old, "energy_consumed"), num(old, "energy_produced")
    nc, np_ = num(new, "energy_consumed"), num(new, "energy_produced")
    if None not in (oc, op, nc, np_) and abs(oc - nc) > 1e-3:  # type: ignore[operator]
        swapped = abs(oc - np_) < 1e-3 and abs(op - nc) < 1e-3  # type: ignore[operator]
        outcomes.append("counters swapped by the old parser" if swapped else "counters differ")
    for name in ("power_consumed", "power_produced"):
        a, b = num(old, name), num(new, name)
        if a is None or b is None or abs(a - b) > 1e-3:
            outcomes.append(f"{name} differs")
    old_gas, new_gas = num(old, "gas_delivered"), num(new, "gas_delivered")
    if new_gas is None:
        outcomes.append("gas 0 → absent" if old_gas == 0 else "gas missing in new")
    elif old_gas is None or abs(old_gas - new_gas) > 1e-3:
        outcomes.append("gas differs")
    return (
        outcomes
        if any(o != "written with crc_valid=false" for o in outcomes)
        else [
            *outcomes,
            "identical",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument(
        "--log-timezone", default="UTC", help="time zone of the old log's timestamps (default UTC)"
    )
    parser.add_argument("--prefixes", default="2019-ETI-EMON-V01")
    parser.add_argument(
        "--strict-crc", action="store_true", help="reject telegrams with a wrong CRC"
    )
    args = parser.parse_args(argv)

    zone = ZoneInfo(args.log_timezone)
    metrics = Metrics()
    reporter = ErrorReporter.create(metrics, limit_per_hour=0, quarantine_file=None)
    pipeline = Pipeline(
        DecoderRegistry(args.prefixes.split(",")),
        TelegramParser(require_valid_crc=args.strict_crc),
        PointBuilder("dsmr"),
        reporter,
        metrics,
    )
    kits: dict[str, Kit] = defaultdict(Kit)
    totals: Counter[str] = Counter()

    pending: tuple[list[str], str | None] | None = None  # new lines, signature
    for path in args.logs:
        with path.open(encoding="utf-8", errors="replace") as file:
            for raw_line in file:
                match = _LINE.match(raw_line.rstrip("\n"))
                if not match:
                    continue
                stamp, kind, rest = match.groups()
                if kind == "MQTT":
                    if pending is not None:  # the old parser failed on the previous message
                        new_lines, sig = pending
                        label = "both rejected" if not new_lines else "old rejected, new wrote"
                        totals[label] += 1
                        if sig:
                            kits[sig].outcomes[label] += 1
                    received = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
                    new_lines = pipeline.process(rest.encode(), received)
                    sig_match = re.search(r'"signature":"([^"]+)"', rest)
                    sig = sig_match.group(1) if sig_match else None
                    if sig and new_lines:
                        info = parse_fields(new_lines[0].split(" ", 1)[1].rsplit(" ", 1)[0])
                        kits[sig].meter = info.get("meter_manufacturer", '"-"').strip('"')
                        kits[sig].version = info.get("dsmr_version", '"-"').strip('"')
                    pending = (new_lines, sig)
                elif pending is not None:
                    old = _OLD.match(rest)
                    new_lines, sig = pending
                    pending = None
                    if not old:
                        continue
                    for outcome in compare(parse_fields(old.group(2)), new_lines):
                        totals[outcome] += 1
                        kits[old.group(1)].outcomes[outcome] += 1

    snap = metrics.snapshot()
    print(
        f"messages: {snap.counters['messages']}  kits: {len(kits)}  "
        f"new points: {snap.counters['points']}  new rejected: {snap.counters['rejected']}"
    )
    print("new errors by category:", dict(snap.errors))
    print("unknown OBIS codes:", dict(snap.unknown_codes.most_common(10)))
    print("\noutcomes:")
    for outcome, count in totals.most_common():
        print(f"  {count:7d}  {outcome}")
    print("\nkits with differences other than gas 0 → absent:")
    for sig, kit in sorted(kits.items(), key=lambda kv: kv[1].meter):
        notable = {
            k: v
            for k, v in kit.outcomes.items()
            if k not in ("identical", "gas 0 → absent", "written with crc_valid=false")
        }
        if notable:
            print(f"  {mask(sig)}  meter={kit.meter} version={kit.version}  {notable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
