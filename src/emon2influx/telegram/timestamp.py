"""Meter timestamps: `YYMMDDhhmmss` plus `S` (summer, UTC+2) or `W` (winter, UTC+1).

Old meters (DSMR 2.2/3) leave out the letter; their time is Dutch local time.
"""

import re
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
_SUMMER = timezone(timedelta(hours=2))
_WINTER = timezone(timedelta(hours=1))
_PATTERN = re.compile(r"(\d{12})([SW]?)")


def parse_meter_time(text: str) -> datetime | None:
    """The moment in UTC, or None when `text` is not a valid meter timestamp."""
    match = _PATTERN.fullmatch(text.strip())
    if match is None:
        return None
    digits, season = match.groups()
    try:
        local = datetime.strptime(digits, "%y%m%d%H%M%S")
    except ValueError:
        return None
    zone = {"S": _SUMMER, "W": _WINTER}.get(season, AMSTERDAM)
    return local.replace(tzinfo=zone).astimezone(UTC)
