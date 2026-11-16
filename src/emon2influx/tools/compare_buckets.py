"""Compares what the old parser wrote (reference bucket) with the new parser (test bucket).

    python -m emon2influx.tools.compare_buckets --hours 24 \\
        --reference-env ~/p1-meter-app/.env

Per kit over the same window it compares the number of electricity points, the import and
export use (counter deltas), the mean power and the last gas reading. The old parser swapped
the counters for meters without a version line; that is recognised and reported as such.
Signatures in the report are masked. Read-only: nothing is written.

The test bucket is read with the settings in `.env`. The reference bucket is read with
`REFERENCE_INFLUX_URL`, `REFERENCE_INFLUX_ORG`, `REFERENCE_INFLUX_BUCKET` and
`REFERENCE_INFLUX_TOKEN`, or with `INFLUX_URL`/`INFLUX_ORG`/`INFLUX_BUCKET`/`INFLUX_TOKEN` from
the file given with `--reference-env` (e.g. the read-only settings of the p1-meter-app).
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import dotenv_values
from influxdb_client.client.influxdb_client import InfluxDBClient

from ..signature import mask


@dataclass
class Source:
    url: str
    org: str
    bucket: str
    token: str


@dataclass
class KitStats:
    points: int = 0
    import_kwh: float | None = None
    export_kwh: float | None = None
    power_mean: float | None = None
    gas_last: float | None = None


def _flux(bucket: str, start: datetime, stop: datetime, measurement: str) -> str:
    return (
        f'data = from(bucket: "{bucket}") |> range(start: {start.isoformat()}, '
        f"stop: {stop.isoformat()}) "
        f'|> filter(fn: (r) => r._measurement == "{measurement}")\n'
    )


def collect(
    source: Source, start: datetime, stop: datetime, measurement: str
) -> dict[str, KitStats]:
    base = _flux(source.bucket, start, stop, measurement)
    queries = {
        "points": 'data |> filter(fn: (r) => r._field == "power_consumed") |> count()',
        "spread": 'data |> filter(fn: (r) => r._field == "energy_consumed" or '
        'r._field == "energy_produced") |> spread()',
        "power": 'data |> filter(fn: (r) => r._field == "power_consumed") |> mean()',
        "gas": 'data |> filter(fn: (r) => r._field == "gas_delivered") |> last()',
    }
    kits: dict[str, KitStats] = {}
    with InfluxDBClient(url=source.url, token=source.token, org=source.org, timeout=120_000) as c:
        api = c.query_api()
        # Hourly gas readings sit at their capture time, up to an hour before the window.
        gas_base = _flux(source.bucket, start - timedelta(hours=2), stop, measurement)
        for name, query in queries.items():
            for table in api.query((gas_base if name == "gas" else base) + query):
                for record in table.records:
                    kit = kits.setdefault(str(record["signature"]), KitStats())
                    value = record.get_value()
                    if name == "points":
                        kit.points = int(value)
                    elif name == "spread":
                        if record.get_field() == "energy_consumed":
                            kit.import_kwh = float(value)
                        else:
                            kit.export_kwh = float(value)
                    elif name == "power":
                        kit.power_mean = float(value)
                    else:
                        kit.gas_last = float(value)
    return kits


def first_point(
    source: Source, start: datetime, stop: datetime, measurement: str
) -> datetime | None:
    query = _flux(source.bucket, start, stop, measurement) + (
        'data |> filter(fn: (r) => r._field == "power_consumed") |> first() |> group() '
        '|> min(column: "_time")'
    )
    with InfluxDBClient(url=source.url, token=source.token, org=source.org, timeout=120_000) as c:
        for table in c.query_api().query(query):
            for record in table.records:
                moment = record.get_time()
                if isinstance(moment, datetime):
                    return moment.replace(microsecond=0) + timedelta(minutes=1)
    return None


def close(a: float | None, b: float | None, tolerance: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tolerance


def judge(old: KitStats, new: KitStats) -> list[str]:
    """Differences for one kit; an empty list means the data matches."""
    notes: list[str] = []
    if old.points and abs(old.points - new.points) > max(3, 0.02 * old.points):
        notes.append(f"points old={old.points} new={new.points}")
    use_ok = close(old.import_kwh, new.import_kwh, 0.01) and close(
        old.export_kwh, new.export_kwh, 0.01
    )
    swapped = close(old.import_kwh, new.export_kwh, 0.01) and close(
        old.export_kwh, new.import_kwh, 0.01
    )
    if not use_ok:
        notes.append(
            "counters swapped by the old parser (expected)"
            if swapped and not use_ok
            else f"use old={old.import_kwh}/{old.export_kwh} "
            f"new={new.import_kwh}/{new.export_kwh} kWh"
        )
    if not close(old.power_mean, new.power_mean, 0.02 + 0.02 * abs(old.power_mean or 0)):
        notes.append(f"mean power old={old.power_mean} new={new.power_mean} kW")
    if old.gas_last == 0 and new.gas_last is None:
        pass  # no gas meter: old wrote 0, new writes nothing (expected)
    elif not close(old.gas_last, new.gas_last, 0.001):
        notes.append(f"gas old={old.gas_last} new={new.gas_last} m3")
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument(
        "--end-minutes-ago",
        type=float,
        default=10,
        help="end of the window, to leave room for late writes",
    )
    parser.add_argument("--reference-env", type=Path)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)

    test_env = {k: (v or "").strip() for k, v in dotenv_values(args.env).items()}
    ref_env = (
        {k: (v or "").strip() for k, v in dotenv_values(args.reference_env).items()}
        if args.reference_env
        else {}
    )

    def ref(key: str) -> str:
        return os.environ.get(f"REFERENCE_{key}") or ref_env.get(key) or ""

    test = Source(
        test_env.get("INFLUX_URL", ""),
        test_env.get("INFLUX_ORG", ""),
        test_env.get("INFLUX_BUCKET", ""),
        test_env.get("INFLUX_TOKEN", ""),
    )
    reference = Source(
        ref("INFLUX_URL"), ref("INFLUX_ORG"), ref("INFLUX_BUCKET"), ref("INFLUX_TOKEN")
    )
    missing = [
        n
        for n, s in (("test", test), ("reference", reference))
        if not (s.url and s.org and s.bucket and s.token)
    ]
    if missing:
        print(f"missing InfluxDB settings for: {', '.join(missing)}", file=sys.stderr)
        return 2
    measurement = test_env.get("INFLUX_MEASUREMENT") or "dsmr"
    stop = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=args.end_minutes_ago)
    start = stop - timedelta(hours=args.hours)

    # Only compare the time both parsers were running.
    first = first_point(test, start, stop, measurement)
    if first is None:
        print("the test bucket has no data in this window", file=sys.stderr)
        return 2
    start = max(start, first)
    old = collect(reference, start, stop, measurement)
    new = collect(test, start, stop, measurement)
    print(f"window {start:%Y-%m-%d %H:%M} - {stop:%Y-%m-%d %H:%M} UTC")
    print(
        f"reference {reference.bucket}: {len(old)} kits, "
        f"{sum(k.points for k in old.values())} points"
    )
    print(f"test      {test.bucket}: {len(new)} kits, {sum(k.points for k in new.values())} points")

    matched = expected = 0
    problems: list[str] = []
    for signature in sorted(set(old) | set(new)):
        if signature not in new:
            problems.append(f"  {mask(signature)}  only in reference ({old[signature].points} pts)")
            continue
        if signature not in old:
            problems.append(f"  {mask(signature)}  only in test ({new[signature].points} pts)")
            continue
        notes = judge(old[signature], new[signature])
        if not notes:
            matched += 1
        elif notes == ["counters swapped by the old parser (expected)"]:
            expected += 1
            print(f"  {mask(signature)}  {notes[0]}")
        else:
            problems.append(f"  {mask(signature)}  " + "; ".join(notes))
    print(
        f"\nmatching: {matched}  expected differences: {expected}  "
        f"other differences: {len(problems)}"
    )
    for line in problems:
        print(line)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
