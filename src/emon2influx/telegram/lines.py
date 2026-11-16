"""Sorts the data lines of a telegram: electricity lines by OBIS code, M-Bus lines per channel."""

from dataclasses import dataclass, field

from .model import FieldIssue
from .obis import ObisLine, join_continuations, parse_line


@dataclass
class LineIndex:
    electricity: dict[str, ObisLine] = field(default_factory=dict)
    channels: dict[int, list[ObisLine]] = field(default_factory=dict)
    issues: list[FieldIssue] = field(default_factory=list)

    @classmethod
    def build(cls, lines: list[str]) -> "LineIndex":
        index = cls()
        for raw in join_continuations(lines):
            line = parse_line(raw)
            if line is None:
                index.issues.append(FieldIssue("field", "unreadable line", raw))
            elif line.medium == 0 and line.channel > 0:
                index.channels.setdefault(line.channel, []).append(line)
            elif line.code in index.electricity:
                index.issues.append(FieldIssue("field", "duplicate line ignored", raw))
            else:
                index.electricity[line.code] = line
        return index

    def first_text(self, code: str) -> str | None:
        """The first value of a line, stripped; None when the line or value is absent."""
        line = self.electricity.get(code)
        if line is None or not line.values:
            return None
        return line.values[0].text.strip() or None
