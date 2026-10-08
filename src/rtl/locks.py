"""File locks so parallel workstreams can share one checkout safely.

- `manifest_lock()` serialises read-modify-write of data/manifest.json.
- `heavy_job(name)` lets only one national-scale job run at a time (4 CPU / 15 GB machine).
  Develop on the fixture without it; wrap national runs in it.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from rtl.settings import DATA_DIR

LOCK_DIR = DATA_DIR / ".locks"


@contextmanager
def _flock(name: str, label: str = "") -> Iterator[None]:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCK_DIR / f"{name}.lock"
    with open(path, "a+") as f:
        t0 = time.time()
        fcntl.flock(f, fcntl.LOCK_EX)  # blocks until free
        waited = time.time() - t0
        if waited > 5:
            print(f"[lock:{name}] acquired after {waited:.0f}s wait")
        f.seek(0), f.truncate(), f.write(f"{os.getpid()} {label}\n"), f.flush()
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def manifest_lock() -> AbstractContextManager[None]:
    return _flock("manifest")


def heavy_job(label: str) -> AbstractContextManager[None]:
    return _flock("heavy", label)
