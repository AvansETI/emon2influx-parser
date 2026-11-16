"""Error categories (doc/architecture.md §8)."""

from enum import StrEnum


class Category(StrEnum):
    PAYLOAD_JSON = "payload_json"
    PAYLOAD_SHAPE = "payload_shape"
    PAYLOAD_UNSUPPORTED = "payload_unsupported"
    SIGNATURE = "signature"
    CRC = "crc"
    TELEGRAM = "telegram"
    FIELD = "field"
    MBUS_TIME = "mbus_time"
    INFLUX_REJECTED = "influx_rejected"
    SPOOL_DROPPED = "spool_dropped"
    QUEUE_DROPPED = "queue_dropped"


class RejectedMessage(Exception):
    """A message that cannot be processed at all; nothing of it is written."""

    def __init__(self, category: Category, message: str, *, line: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.line = line
