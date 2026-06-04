"""A shared background event loop for running async code from sync tool handlers.

Hermes tool handlers and CLI commands are synchronous, but the x402 SDK (httpx
client, MCP client) and the CDP SDK are async-first. Rather than spinning up a fresh
``asyncio.run`` per call (which forbids nesting and re-creates clients), we keep one
daemon-thread event loop alive for the process and submit coroutines to it. This mirrors
the pattern Hermes uses for its own MCP background loop.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import TimeoutError as _FuturesTimeout
from typing import Any, Coroutine, TypeVar

_T = TypeVar("_T")

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


class UnknownSettlementError(Exception):
    """A paid operation exceeded its deadline and was cancelled mid-flight.

    Because cancellation is best-effort, the payment may or may not have settled. Callers
    must treat this as "unknown" (fail closed) rather than a clean failure.
    """


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop

        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run, name="hermes-x402-loop", daemon=True)
        thread.start()
        _loop, _thread = loop, thread
        return loop


def run_async(coro: Coroutine[Any, Any, _T], *, timeout: float | None = 120.0) -> _T:
    """Run ``coro`` on the shared background loop and block for its result.

    Safe to call from any (sync) thread. Use for one-shot async operations such as CDP
    account provisioning, a paid HTTP request, or a paid MCP tool call.
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except _FuturesTimeout:
        # Ask the loop to cancel the still-running coroutine (best-effort) so it cannot keep
        # working — but a payment may already be in flight, so surface "unknown settlement".
        future.cancel()
        raise UnknownSettlementError(
            f"operation exceeded {timeout}s and was cancelled; settlement status is unknown"
        ) from None
