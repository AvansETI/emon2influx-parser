"""Writes doc/fields.md and doc/fields.json from the registry (doc/architecture.md §4.3).

`python -m emon2influx.tools.fields` writes them; `--check` fails when they are out of date.
"""

import argparse
import json
import sys
from pathlib import Path

from ..telegram.registry import EXCLUDED, SOURCE_ONLY, field_map, registry_hash

DOC = Path(__file__).resolve().parents[3] / "doc"


def render_json() -> str:
    return (
        json.dumps(
            {"registry": registry_hash(), "fields": field_map(), "never_stored": EXCLUDED},
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|")


def render_markdown() -> str:
    rows = field_map()
    out = [
        "# Fields",
        "",
        "Every field in the `dsmr` measurement and the DSMR/OBIS code it comes from.",
        "Generated from `src/emon2influx/telegram/registry.py` by",
        "`python -m emon2influx.tools.fields`; do not edit by hand.",
        f"Registry version `{registry_hash()}`; the same map is in the `dsmr_fields`",
        "measurement in InfluxDB.",
        "",
        "A field is only written when its code is in the telegram. M-Bus fields",
        "(`point: mbus`) are separate points at the capture time from the telegram; the",
        "others share one point at the receipt time.",
        "",
        "| Field | OBIS | Unit | Type | Point | Occurs | Description |",
        "| ----- | ---- | ---- | ---- | ----- | ------ | ----------- |",
    ]
    for row in rows:
        codes = ", ".join(f"`{c}`" for c in row["obis"])  # type: ignore[attr-defined]
        derived = " (derived)" if row.get("derived") else ""
        out.append(
            f"| `{row['field']}` | {codes}{derived} | {_cell(row['unit'] or '-')} | {row['type']} "
            f"| {row['point']} | {_cell(row['occurs'])} | {_cell(row['description'])} |"
        )
    out += [
        "",
        "## Read but not stored under their own name",
        "",
        "| OBIS | Meaning |",
        "| ---- | ------- |",
        *(f"| `{code}` | {_cell(text)} |" for code, text in SOURCE_ONLY.items()),
        "",
        "## Never stored",
        "",
        "These would lead to the household or carry free text.",
        "",
        "| OBIS | Meaning |",
        "| ---- | ------- |",
        *(f"| `{code}` | {_cell(text)} |" for code, text in EXCLUDED.items()),
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when the files are outdated")
    args = parser.parse_args(argv)
    files = {DOC / "fields.md": render_markdown(), DOC / "fields.json": render_json()}
    if args.check:
        stale = [p.name for p, text in files.items() if not p.exists() or p.read_text() != text]
        if stale:
            print(f"outdated: {', '.join(stale)}; run python -m emon2influx.tools.fields")
            return 1
        return 0
    for path, text in files.items():
        path.write_text(text)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
