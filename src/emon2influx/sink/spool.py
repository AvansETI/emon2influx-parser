"""Write-ahead spool on disk for batches InfluxDB did not accept yet (doc/architecture.md §7).

One file per batch, in line protocol, replayed oldest first. A file is complete or absent: it
is written under a temporary name, synced and then renamed.
"""

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("emon2influx")


class DiskSpool:
    def __init__(self, directory: Path, max_bytes: int) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._seq = 0
        directory.mkdir(parents=True, exist_ok=True)
        for leftover in directory.glob("*.tmp"):
            leftover.unlink(missing_ok=True)
        self._bytes = sum(p.stat().st_size for p in self._files())

    def _files(self) -> list[Path]:
        return sorted(self.directory.glob("*.lp"))

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def is_empty(self) -> bool:
        with self._lock:
            return not self._files()

    def count_files(self) -> int:
        with self._lock:
            return len(self._files())

    def append(self, lines: list[str]) -> int:
        """Stores a batch; returns the number of lines dropped to stay under the size cap."""
        if not lines:
            return 0
        data = ("\n".join(lines) + "\n").encode()
        with self._lock:
            self._seq = (self._seq + 1) % 1_000_000
            name = f"{time.time_ns():020d}-{self._seq:06d}"
            tmp = self.directory / f"{name}.tmp"
            with tmp.open("wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            tmp.rename(self.directory / f"{name}.lp")
            self._sync_dir()
            self._bytes += len(data)
            return self._enforce_cap()

    def oldest(self) -> Path | None:
        with self._lock:
            files = self._files()
            return files[0] if files else None

    @staticmethod
    def read(path: Path) -> list[str]:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line]

    def remove(self, path: Path) -> None:
        with self._lock:
            try:
                size = path.stat().st_size
                path.unlink()
                self._bytes -= size
            except FileNotFoundError:
                pass

    def _enforce_cap(self) -> int:
        dropped = 0
        files = self._files()
        while self._bytes > self.max_bytes and len(files) > 1:
            oldest = files.pop(0)
            dropped += len(self.read(oldest))
            self._bytes -= oldest.stat().st_size
            oldest.unlink()
        return dropped

    def _sync_dir(self) -> None:
        try:
            fd = os.open(self.directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
