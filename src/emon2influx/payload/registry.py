"""Raw MQTT bytes → validated `Payload` with its `Signature` (§3)."""

import json
from collections.abc import Iterable, Mapping
from datetime import datetime

from ..errors import Category, RejectedMessage
from ..signature import Signature
from .base import Payload, PayloadDecoder
from .emon_v1 import EmonV1Decoder

# Signature prefix → the decoder of that kit generation. V02 is added once its format is known.
DECODERS: Mapping[str, PayloadDecoder] = {"2019-ETI-EMON-V01": EmonV1Decoder()}


class DecoderRegistry:
    def __init__(
        self, prefixes: Iterable[str], decoders: Mapping[str, PayloadDecoder] = DECODERS
    ) -> None:
        self.prefixes = tuple(prefixes)
        self.decoders = decoders

    def decode(self, raw: bytes, received_at: datetime) -> tuple[Payload, Signature]:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RejectedMessage(Category.PAYLOAD_JSON, f"invalid UTF-8: {error.reason}") from None
        try:
            message = json.loads(text)
        except json.JSONDecodeError as error:
            raise RejectedMessage(Category.PAYLOAD_JSON, f"invalid JSON: {error.msg}") from None
        if not isinstance(message, dict):
            # The broker and kits also publish bare values such as `1`; expected noise.
            raise RejectedMessage(Category.PAYLOAD_SHAPE, f"not a JSON object: {text[:20]!r}")

        decoder = next((d for d in self.decoders.values() if d.matches(message)), None)
        if decoder is None:
            raise RejectedMessage(Category.PAYLOAD_UNSUPPORTED, "unknown payload format")
        payload = decoder.decode(message, received_at)
        signature = Signature.parse(payload.signature_raw, self.prefixes)
        if self.decoders.get(signature.prefix) is not decoder:
            raise RejectedMessage(
                Category.PAYLOAD_UNSUPPORTED,
                f"no {decoder.name} decoder for prefix {signature.prefix}",
            )
        return payload, signature
