"""One reported problem."""

from dataclasses import dataclass

from ..errors import Category


@dataclass(frozen=True)
class ErrorEvent:
    category: Category
    message: str
    signature: str | None = None
    meter: str | None = None
    version: str | None = None
    line: str | None = None
