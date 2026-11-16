"""Sends the spooled batches to InfluxDB, oldest first, within a time budget (§7).

A file is removed only after InfluxDB accepted it (or rejected the data itself), so a crash
during replay sends it again; InfluxDB overwrites identical points, so that is harmless.
"""

import time
from collections.abc import Callable

from .backoff import BackoffPolicy
from .delivery import Delivery, Outcome
from .spool import DiskSpool


class SpoolReplayer:
    def __init__(
        self,
        spool: DiskSpool,
        delivery: Delivery,
        backoff: BackoffPolicy,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.spool = spool
        self.delivery = delivery
        self.backoff = backoff
        self.clock = clock

    def replay(self, budget_s: float) -> bool:
        """True when the spool is empty afterwards."""
        deadline = self.clock() + budget_s
        while self.clock() < deadline and self.backoff.ready():
            path = self.spool.oldest()
            if path is None:
                return True
            outcome = self.delivery.send(self.spool.read(path))
            if outcome is Outcome.RETRY:
                self.backoff.failed()
                return False
            self.backoff.succeeded()
            self.spool.remove(path)
        return self.spool.is_empty()
