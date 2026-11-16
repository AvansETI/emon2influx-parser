"""Electricity lines → fields, driven by the registry (doc/architecture.md §4.1)."""

from .lines import LineIndex
from .model import FieldIssue, Value
from .obis import ObisLine
from .registry import BY_OBIS, EXCLUDED, SOURCE_ONLY, FieldSpec


def convert(spec: FieldSpec, line: ObisLine) -> Value | FieldIssue:
    """The stored value of a line (last group, unit converted), or why it cannot be used."""
    if not line.values:
        return FieldIssue("field", f"{spec.field}: no value", line.raw)
    value = line.values[-1]
    if spec.units:
        factor = spec.units.get(value.unit or "")
        if factor is None:
            return FieldIssue("field", f"{spec.field}: unexpected unit {value.unit!r}", line.raw)
    elif value.unit is not None:
        return FieldIssue("field", f"{spec.field}: unexpected unit {value.unit!r}", line.raw)
    else:
        factor = 1.0
    try:
        if spec.kind == "int":
            return int(value.text)
        return round(float(value.text) * factor, 6)
    except ValueError:
        return FieldIssue("field", f"{spec.field}: unreadable value {value.text!r}", line.raw)


class ElectricityMapper:
    """Reads every registered electricity field; collects codes it does not know."""

    def map(self, index: LineIndex) -> tuple[dict[str, Value], list[FieldIssue], list[str]]:
        fields: dict[str, Value] = {}
        issues: list[FieldIssue] = []
        unknown: list[str] = []
        for code, line in index.electricity.items():
            spec = BY_OBIS.get(code)
            if spec is None:
                if code not in SOURCE_ONLY and code not in EXCLUDED:
                    unknown.append(code)
                continue
            result = convert(spec, line)
            if isinstance(result, FieldIssue):
                issues.append(result)
            else:
                fields[spec.field] = result
        return fields, issues, unknown
