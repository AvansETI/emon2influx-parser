"""Removes `gas_delivered=0` from households without a gas meter (doc/architecture.md §13.1).

    python -m emon2influx.tools.drop_zero_gas plan    --env FILE --until ISO
    python -m emon2influx.tools.drop_zero_gas apply   --env FILE --plan gas-plan.json --yes
    python -m emon2influx.tools.drop_zero_gas restore --env FILE --backup DIR --yes

- plan     read-only: households whose gas was 0 in every point, up to now. Writes the plan.
- apply    per day: back up all points, delete them, write them back without gas, check.
           Safe to run again after an interruption; finished days are skipped.
- restore  writes a household's backups (one directory per household) back as they were.

--env names a file with INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET and INFLUX_TOKEN; deleting needs
write access. Writing to p1-smartmeters also needs --allow-production. Output masks signatures;
the plan and backups hold them in full and stay private (git-ignored).
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from ...signature import mask
from ..fix_orientation.__main__ import PRODUCTION, moment, open_store
from .core import GasPlan, GasRewriter, make_plan


def guard(bucket: str, args: argparse.Namespace) -> None:
    if not args.yes:
        raise SystemExit("this deletes and rewrites data in InfluxDB; add --yes after review")
    if bucket == PRODUCTION and not args.allow_production:
        raise SystemExit(f"{PRODUCTION} is the production bucket; add --allow-production")


def cmd_plan(args: argparse.Namespace) -> int:
    store = open_store(args.env, args.bucket)
    plan = make_plan(store, moment(args.start), moment(args.until), datetime.now(UTC))
    for sig, count in plan.households.items():
        print(f"  {mask(sig)}  {count} gas points, all 0")
    plan.save(args.out)
    print(f"{len(plan.households)} household(s) without a gas meter; plan written to {args.out}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    plan = GasPlan.load(args.plan)
    store = open_store(args.env, plan.bucket)
    guard(store.bucket, args)
    rewriter = GasRewriter(store, args.backups)
    for sig in plan.households:
        days, removed = rewriter.rewrite(sig, moment(plan.since), moment(plan.until))
        print(f"  {mask(sig)}  {days} day(s) rewritten, {removed} gas points removed")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    store = open_store(args.env)
    guard(store.bucket, args)
    rewriter = GasRewriter(store, Path())
    for directory in args.backup:
        sig, days = rewriter.restore(directory)
        print(f"  {mask(sig)}  {days} day(s) restored")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m emon2influx.tools.drop_zero_gas",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--bucket")
    p.add_argument("--from", dest="start", default="2023-08-01T00:00:00+00:00")
    p.add_argument("--until", required=True, help="switch to the new parser (ISO 8601)")
    p.add_argument("--out", type=Path, default=Path("gas-plan.json"))
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("apply")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--plan", type=Path, default=Path("gas-plan.json"))
    p.add_argument("--backups", type=Path, default=Path("backups/gas"))
    p.add_argument("--yes", action="store_true")
    p.add_argument("--allow-production", action="store_true")
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("restore")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--backup", type=Path, nargs="+", required=True, help="household directories")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--allow-production", action="store_true")
    p.set_defaults(func=cmd_restore)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
