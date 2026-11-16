"""The outer structure of a P1 telegram: `/` identification, data lines, `!` and CRC."""

import re
from dataclasses import dataclass

from ..errors import Category, RejectedMessage

_CRC = re.compile(r"[0-9A-Fa-f]{4}")


def crc16(data: bytes) -> int:
    """CRC-16/ARC (polynomial 0xA001 reflected, initial 0), as in DSMR 4 and later."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass(frozen=True)
class TelegramFrame:
    identification: str
    lines: list[str]
    crc: int | None

    @classmethod
    def parse(cls, text: str, *, require_valid_crc: bool) -> "TelegramFrame":
        start = text.find("/")
        end = text.find("!", start + 1)
        if start < 0:
            raise RejectedMessage(Category.TELEGRAM, "no '/' at the start of the telegram")
        if end < 0:
            raise RejectedMessage(
                Category.TELEGRAM, "no '!' at the end of the telegram (truncated)"
            )

        trailer = text[end + 1 :].strip()
        crc: int | None = None
        if trailer:
            if not _CRC.fullmatch(trailer[:4]):
                raise RejectedMessage(Category.CRC, "unreadable CRC", line="!" + trailer[:8])
            crc = int(trailer[:4], 16)
            body = text[start : end + 1].encode("latin-1", "replace")
            if require_valid_crc and crc16(body) != crc:
                raise RejectedMessage(
                    Category.CRC,
                    f"CRC mismatch: telegram says {crc:04X}, computed {crc16(body):04X}",
                )

        content = text[start + 1 : end].replace("\r", "").split("\n")
        identification = content[0].strip()
        if not identification:
            raise RejectedMessage(Category.TELEGRAM, "empty identification line")
        return cls(identification, [line for line in content[1:] if line.strip()], crc)

    @property
    def manufacturer(self) -> str | None:
        """The 3-letter manufacturer code; the rest of the line may hold a serial number."""
        code = self.identification[:3]
        return code if len(code) == 3 and code.isalpha() else None
