"""Process-scoped Windows file locks for Editor startup and Remote transport."""

from __future__ import annotations

import hashlib
import msvcrt
import os
import tempfile
import time
from pathlib import Path


LOCK_ROOT = Path(tempfile.gettempdir()) / "ue-editor-remote-exec-locks"


def machine_lock_path(name: str) -> Path:
    return LOCK_ROOT / name


def project_launch_lock_path(project: str) -> Path:
    normalized = os.path.normcase(os.path.realpath(os.path.abspath(project)))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return LOCK_ROOT / f"launch-{digest}.lock"


class ResourceLease:
    """Windows file lock automatically released if the owning process exits."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._stream = None

    def acquire(
        self,
        timeout: float = 0.0,
        poll_interval: float = 0.05,
    ) -> bool:
        if self._stream is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                self._stream = stream
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    stream.close()
                    return False
                time.sleep(
                    min(
                        float(poll_interval),
                        max(0.0, deadline - time.monotonic()),
                    )
                )

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.seek(0)
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "ResourceLease":
        if not self.acquire():
            raise RuntimeError(f"resource is already in use: {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False
