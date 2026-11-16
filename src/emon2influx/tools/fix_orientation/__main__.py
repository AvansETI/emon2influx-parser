"""One-off correction of swapped energy counters in history (doc/architecture.md §13).

    python -m emon2influx.tools.fix_orientation plan    --env FILE --until ISO [--signature S]...
    python -m emon2influx.tools.fix_orientation apply   --env FILE --plan plan.json --yes
    python -m emon2influx.tools.fix_orientation verify  --env FILE --plan plan.json
    python -m emon2influx.tools.fix_orientation restore --env FILE --backup FILE... --yes
    python -m emon2influx.tools.fix_orientation copy    --source-env FILE --env FILE
                                                        --signature S --from ISO --until ISO

- plan     read-only: decides per day which counter held the import and lists the swapped
           periods before --until (the switch to the new parser). Writes plan.json.
- apply    backs up each period (gzipped line protocol in --backups), writes the swapped
           values from that backup, records it in `dsmr_corrections` and verifies. Safe to run
           again after an interruption.
- verify   the votes per period now (after apply they should say as-named).
- restore  writes the original values of backups back.
- copy     rehearsal: copies a household's history into a test bucket.

--env names a file with INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET, INFLUX_TOKEN (and optionally
INFLUX_MEASUREMENT), e.g. the parser's .env. Writing to the production bucket p1-smartmeters
also needs --allow-production. Output masks signatures; plan.json and the backups contain them
in full, so keep those files private (they are git-ignored).
"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from ...signature import mask
from . import apply as apply_mod
from .plan import Plan, make_plan
from .store import InfluxStore, checked

PRODUCTION = "p1-smartmeters"
FIELDS = (
    "power_consumed",
    "power_produced",
    "energy_consumed",
    "energy_produced",
    "gas_delivered",
)


def open_store(env_file: Path, bucket: str | None = None) -> InfluxStore:
    env = {k: (v or "").strip() for k, v in dotenv_values(env_file).items()}
    missing = [k for k in ("INFLUX_URL", "INFLUX_ORG", "INFLUX_TOKEN") if not env.get(k)]
    if missing or not (bucket or env.get("INFLUX_BUCKET")):
        raise SystemExit(f"{env_file}: missing {', '.join(missing) or 'INFLUX_BUCKET'}")
    return InfluxStore(
        env["INFLUX_URL"],
        env["INFLUX_ORG"],
        bucket or env["INFLUX_BUCKET"],
        env["INFLUX_TOKEN"],
        env.get("INFLUX_MEASUREMENT") or "dsmr",
    )


def moment(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def guard_writes(store: InfluxStore, args: argparse.Namespace) -> None:
    if not args.yes:
        raise SystemExit("this writes to InfluxDB; add --yes after reviewing the plan")
    if store.bucket == PRODUCTION and not args.allow_production:
        raise SystemExit(f"{PRODUCTION} is the production bucket; add --allow-production")


def cmd_plan(args: argparse.Namespace) -> int:
    store = open_store(args.env, args.bucket)
    start, until = moment(args.start), moment(args.until)
    signatures = [checked(s) for s in args.signature] or store.signatures(start, until)
    print(f"bucket {store.bucket}: {len(signatures)} household(s), {start:%Y-%m-%d} - {until}")
    plan = make_plan(store, signatures, start, until)
    for household in plan.households:
        print(f"\n{mask(household.signature)}")
        for p in household.periods:
            print(
                f"  correct  {p.first_day} - {p.last_day}  {p.days} days, "
                f"{p.voted_days} clear days, agreement {p.agreement:.0%}"
            )
        for line in household.review:
            print(f"  review   {line}")
        for line in household.skipped:
            print(f"  skip     {line}")
    plan.save(args.out)
    print(f"\n{len(plan.periods())} period(s) to correct; plan written to {args.out}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    plan = Plan.load(args.plan)
    store = open_store(args.env, plan.bucket)
    guard_writes(store, args)
    for period in plan.periods():
        if apply_mod.already_corrected(store, period):
            print(
                f"{mask(period.signature)} {period.first_day} - {period.last_day}: "
                "already corrected, skipped"
            )
            continue
        result = apply_mod.apply_period(store, period, args.backups, store.measurement)
        print(
            f"{mask(period.signature)} {period.first_day} - {period.last_day}: "
            f"{result.points} points swapped; backup {result.backup.name}; "
            f"votes now {result.verified}"
        )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    plan = Plan.load(args.plan)
    store = open_store(args.env, plan.bucket)
    for period in plan.periods():
        print(
            f"{mask(period.signature)} {period.first_day} - {period.last_day}: "
            f"{apply_mod.verify(store, period)}"
        )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    store = open_store(args.env)
    guard_writes(store, args)
    for path in args.backup:
        period, points = apply_mod.restore(store, path)
        print(f"{mask(period.signature)} {period.start} - {period.stop}: {points} points restored")
    return 0


def cmd_copy(args: argparse.Namespace) -> int:
    source = open_store(args.source_env)
    target = open_store(args.env)
    if target.bucket == PRODUCTION:
        raise SystemExit("copy never writes to the production bucket")
    signature = checked(args.signature)
    lines = source.raw_lines(signature, moment(args.start), moment(args.until), FIELDS)
    count = apply_mod.write_lines(target, lines)
    print(f"{mask(signature)}: {count} points copied from {source.bucket} to {target.bucket}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        prog="python -m emon2influx.tools.fix_orientation",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--bucket", help="override INFLUX_BUCKET")
    p.add_argument("--from", dest="start", default="2023-08-01T00:00:00+00:00")
    p.add_argument("--until", required=True, help="switch to the new parser (ISO 8601)")
    p.add_argument("--signature", action="append", default=[])
    p.add_argument("--out", type=Path, default=Path("plan.json"))
    p.set_defaults(func=cmd_plan)

    for name, func in (("apply", cmd_apply), ("verify", cmd_verify)):
        p = sub.add_parser(name)
        p.add_argument("--env", type=Path, default=Path(".env"))
        p.add_argument("--plan", type=Path, default=Path("plan.json"))
        if name == "apply":
            p.add_argument("--backups", type=Path, default=Path("backups"))
            p.add_argument("--yes", action="store_true")
            p.add_argument("--allow-production", action="store_true")
        p.set_defaults(func=func)

    p = sub.add_parser("restore")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--backup", type=Path, nargs="+", required=True)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--allow-production", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("copy")
    p.add_argument("--source-env", type=Path, required=True)
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--signature", required=True)
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--until", required=True)
    p.set_defaults(func=cmd_copy)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
