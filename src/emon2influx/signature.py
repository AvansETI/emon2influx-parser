"""Household signatures: `<prefix>-XXXXXX-XXXXXX` (doc/architecture.md §3.2)."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .errors import Category, RejectedMessage

_DYNAMIC = re.compile(r"-[0-9A-F]{6}-[0-9A-F]{6}")
_PREFIX = re.compile(r"[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*")


@dataclass(frozen=True)
class Signature:
    value: str
    prefix: str

    @classmethod
    def parse(cls, raw: object, prefixes: Iterable[str]) -> "Signature":
        """Validates `raw` against the allowed prefixes; the only way a tag value is made."""
        if not isinstance(raw, str):
            raise RejectedMessage(Category.SIGNATURE, "signature is not a string")
        for prefix in prefixes:
            if raw.startswith(prefix) and _DYNAMIC.fullmatch(raw[len(prefix) :]):
                return cls(raw, prefix)
        raise RejectedMessage(Category.SIGNATURE, f"invalid signature {mask(raw)!r}")

    def masked(self) -> str:
        return mask(self.value)

    def __str__(self) -> str:
        return self.value


def valid_prefix(prefix: str) -> bool:
    return _PREFIX.fullmatch(prefix) is not None


def mask(raw: str) -> str:
    """Prefix plus the last 4 characters, for logs that may be shared."""
    printable = raw.encode("ascii", "backslashreplace").decode()[:64]
    if len(printable) <= 8:
        return "…"
    if len(printable) <= 13:
        return f"…{printable[-4:]}"
    return f"{printable[:-13].rstrip('-')}-…{printable[-4:]}"
