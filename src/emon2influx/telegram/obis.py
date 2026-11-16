"""Data lines: `A-B:C.D.E(value*unit)(value)...` (IEC 62056-21 / OBIS)."""

import re
from dataclasses import dataclass

_LINE = re.compile(r"(\d+)-(\d+):(\d+\.\d+\.\d+)((?:\([^()]*\))*)")
_GROUP = re.compile(r"\(([^()]*)\)")


@dataclass(frozen=True)
class ObisValue:
    text: str
    unit: str | None


@dataclass(frozen=True)
class ObisLine:
    medium: int
    channel: int
    item: str
    values: tuple[ObisValue, ...]
    raw: str

    @property
    def code(self) -> str:
        """E.g. `1-0:32.7.0`; M-Bus lines keep their channel, e.g. `0-1:24.2.1`."""
        return f"{self.medium}-{self.channel}:{self.item}"

    @property
    def generic_code(self) -> str:
        """M-Bus lines with the channel as `n`, e.g. `0-n:24.2.1`."""
        if self.medium == 0 and self.channel > 0:
            return f"0-n:{self.item}"
        return self.code


def join_continuations(lines: list[str]) -> list[str]:
    """DSMR 2.2/3 put the gas value on its own line, starting with `(`; join it back."""
    joined: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("(") and joined:
            joined[-1] += stripped
        else:
            joined.append(stripped)
    return joined


def parse_line(line: str) -> ObisLine | None:
    match = _LINE.fullmatch(line)
    if match is None:
        return None
    medium, channel, item, groups = match.groups()
    values = []
    for group in _GROUP.findall(groups):
        text, star, unit = group.partition("*")
        values.append(ObisValue(text, unit if star else None))
    return ObisLine(int(medium), int(channel), item, tuple(values), line)
