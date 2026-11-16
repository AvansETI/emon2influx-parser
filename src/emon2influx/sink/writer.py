"""Writers: the real InfluxDB writer and the dry-run writer (Null Object), behind one protocol.

A `WriteError` says whether trying again later can help (doc/architecture.md §7).
"""

import logging
from typing import Protocol

log = logging.getLogger("emon2influx")

# Retried later (data kept): outages, overload and configuration problems that an operator
# can fix (token, bucket). Other 4xx responses mean the data itself is rejected.
RETRYABLE_STATUS = frozenset({401, 403, 404, 408, 429}) | frozenset(range(500, 600))


class WriteError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Writer(Protocol):
    def write(self, lines: list[str]) -> None: ...

    def close(self) -> None: ...


class InfluxWriter:
    def __init__(
        self, url: str, org: str, bucket: str, token: str, timeout_ms: int = 10_000
    ) -> None:
        from influxdb_client.client.influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.bucket = bucket
        self.org = org
        self.client = InfluxDBClient(
            url=url, token=token, org=org, timeout=timeout_ms, retries=False
        )
        self.api = self.client.write_api(write_options=SYNCHRONOUS)

    def write(self, lines: list[str]) -> None:
        from influxdb_client.rest import ApiException

        try:
            self.api.write(bucket=self.bucket, org=self.org, record=lines)
        except ApiException as error:
            status = int(error.status) if error.status else None
            raise WriteError(
                f"InfluxDB answered {status}: {str(error.body)[:300]}",
                retryable=status is None or status in RETRYABLE_STATUS,
                status=status,
            ) from None
        except Exception as error:  # connection refused, DNS, TLS, timeouts
            raise WriteError(f"InfluxDB unreachable: {error}", retryable=True) from None

    def close(self) -> None:
        self.client.close()


class DryRunWriter:
    """MODE=dry-run: accepts everything and writes nothing."""

    def __init__(self) -> None:
        self.lines = 0

    def write(self, lines: list[str]) -> None:
        self.lines += len(lines)
        for line in lines[:3]:
            log.debug("dry-run: %s", line)

    def close(self) -> None:
        pass
