"""Run blocking Nina work off the Qt GUI thread."""

from __future__ import annotations

import atexit
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nina-bg")
atexit.register(_POOL.shutdown, wait=False)


def run_blocking(fn: Callable[[], T], *, timeout: float = 60.0) -> T:
    """Execute *fn* on a small shared pool (safe for ``bus_lock`` / ``collect()``)."""
    fut: Future[T] = _POOL.submit(fn)
    return fut.result(timeout=timeout)
