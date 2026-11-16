"""V01 kits: `{"datagram": {"p1": "...", "signature": "...", "s0": {...}, "s1": {...}}}`."""

from datetime import datetime

from ..errors import Category, RejectedMessage
from .base import Payload, PayloadDecoder


class EmonV1Decoder(PayloadDecoder):
    name = "emon-v1"

    def matches(self, message: object) -> bool:
        return isinstance(message, dict) and isinstance(message.get("datagram"), dict)

    def decode(self, message: object, received_at: datetime) -> Payload:
        assert isinstance(message, dict)
        datagram = message["datagram"]
        p1 = datagram.get("p1")
        signature = datagram.get("signature")
        if not isinstance(p1, str) or not p1:
            raise RejectedMessage(Category.PAYLOAD_SHAPE, "datagram.p1 missing or not a string")
        if not isinstance(signature, str):
            raise RejectedMessage(Category.SIGNATURE, "datagram.signature missing")
        # s0 and s1 (pulse inputs) are not stored.
        return Payload(signature, p1, received_at)
