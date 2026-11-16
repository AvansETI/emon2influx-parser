"""Payload decoders turn one kit generation's MQTT message into a `Payload` (§3.3)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Payload:
    signature_raw: str
    p1: str
    received_at: datetime


class PayloadDecoder(ABC):
    name: str

    @abstractmethod
    def matches(self, message: object) -> bool:
        """True when the parsed JSON has this decoder's shape."""

    @abstractmethod
    def decode(self, message: object, received_at: datetime) -> Payload:
        """The payload; raises `RejectedMessage` when a required part is missing."""
