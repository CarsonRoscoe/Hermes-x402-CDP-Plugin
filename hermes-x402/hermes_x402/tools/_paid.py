"""Shared policy for the paid tools (`x402_request`, `x402_retry_mcp_payment`).

Keeps cap resolution, error mapping, ledger recording, and the idempotency + durable
payment-journal orchestration in one place so the two tools don't copy-paste it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Callable

from .. import config, ledger


def extract_server_error(body: dict | None) -> str | None:
    """Extract the most informative error string from a parsed server response body.

    Checks the common error-field names used across x402 server/facilitator responses,
    in order of specificity. Returns ``None`` when ``body`` is absent or has none of them.
    """
    if not isinstance(body, dict):
        return None
    return (
        body.get("error")
        or body.get("message")
        or body.get("detail")
        or body.get("reason")
    ) or None


def decode_x402_header(raw: str) -> dict | None:
    """Decode an x402 wire header value (base64-encoded JSON) to a dict.

    Tries base64-decode first (both with and without padding correction), then falls back
    to treating the value as raw JSON. Returns ``None`` on any parse failure.
    """
    if not raw:
        return None
    for attempt in (raw, raw + "=="):
        try:
            return json.loads(base64.b64decode(attempt).decode())
        except Exception:
            pass
    try:
        return json.loads(raw)
    except Exception:
        return None


class StructuredToolError(Exception):
    """Raise from a tool's ``run()`` function to short-circuit ``run_journaled`` and return
    a structured error dict as JSON, journaled as ``failed`` (not as a generic exception).

    This lets tool implementations surface rich, classified error objects (e.g. a decoded
    402 response with facilitator details) without losing them to the generic ``payment_error``
    formatter in ``run_journaled``'s catch-all handler.
    """

    def __init__(self, error_dict: dict) -> None:
        super().__init__(error_dict.get("error", "structured_error"))
        self.error_dict = error_dict


#: How long a recorded success can be replayed without re-paying.
REPLAY_TTL_SECONDS = 3600.0


def effective_cap(arg_max: float | None) -> float:
    """Stricter of the per-call cap and the configured ceiling (0 => no cap)."""
    caps = [c for c in (arg_max, config.max_price_usdc()) if c and c > 0]
    return min(caps) if caps else 0.0


def payment_error(exc: Exception, cap: float, label: str) -> dict:
    """Map an exception from a paid call to a JSON-able error dict."""
    name = type(exc).__name__
    if "PaymentExceedsCap" in name:
        return {"error": "payment_exceeds_cap", "max_price_usdc": cap}
    if "UnknownSettlement" in name:
        # Money may or may not have moved; tell the caller not to assume failure.
        return {"error": "unknown_settlement", "detail": str(exc), "reconcile": True}
    if "PaymentVerification" in name:
        return {"error": "payment_unverified", "detail": str(exc)}
    if name in ("TimeoutError", "CancelledError"):
        return {"error": f"{label} timed out before payment", "detail": str(exc)}
    return {"error": f"{label} failed: {exc}"}


def record_paid_call(*, kind: str, amount_usdc: float, endpoint: str, transaction, task_id) -> None:
    """Record a settled payment to the ledger (single seam for HTTP + MCP tools)."""
    ledger.record_payment(
        kind=kind,
        amount_usdc=amount_usdc or 0.0,
        network=config.network(),
        endpoint=endpoint,
        transaction=transaction,
        session_id=task_id,
    )


def operation_fingerprint(*, kind, endpoint, arguments, requirement=None, idempotency_key=None) -> str:
    """Stable identity for a paid operation, used for idempotency/replay.

    A caller-supplied ``idempotency_key`` wins; otherwise we hash the operation's
    (kind, endpoint, normalized arguments, payment requirement).
    """
    if idempotency_key:
        return f"key:{idempotency_key}"
    payload = json.dumps(
        {"kind": kind, "endpoint": endpoint, "args": arguments or {}, "req": requirement or {}},
        sort_keys=True,
        default=str,
    )
    return "fp:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _idempotency_check(fingerprint: str, *, override: bool, replay_success: bool) -> dict | None:
    """Short-circuit dict to return immediately, or None to proceed with a fresh attempt.

    - When ``replay_success`` (an explicit idempotency_key was given), a recent ``succeeded``
      entry replays the cached result WITHOUT re-paying. For auto fingerprints we do not
      replay successes, so repeated calls fetch fresh data and pay again intentionally.
    - A ``pending``/``paid``/``unknown`` entry always blocks (money may have moved or be
      in-flight) unless ``override`` is set.
    """
    row = ledger.journal_lookup(fingerprint)
    if not row:
        return None
    state = row.get("state")
    age = time.time() - (row.get("ts") or 0)
    if replay_success and state == "succeeded" and age <= REPLAY_TTL_SECONDS and row.get("result_json"):
        try:
            cached = json.loads(row["result_json"])
            if isinstance(cached, dict):
                cached["replayed"] = True
                return cached
        except (json.JSONDecodeError, TypeError):
            return None
    if state in ledger.JOURNAL_OPEN_STATES and not override:
        return {
            "error": "prior_attempt_incomplete",
            "state": state,
            "message": (
                "a prior attempt for this exact operation paid or is in-flight and did not "
                "confirm; pass override=true to retry (this may pay again)."
            ),
        }
    return None


def run_journaled(
    *,
    kind: str,
    endpoint: str,
    arguments,
    requirement=None,
    idempotency_key: str | None,
    override: bool,
    cap: float,
    label: str,
    session_id,
    run: Callable[[], tuple[dict, float | None, str | None]],
) -> str:
    """Run a paid operation with idempotency + a durable write-ahead journal.

    ``run`` performs the actual paid call and returns ``(output_dict, amount_usdc, tx)`` or
    raises. Returns the JSON string the tool should hand back.
    """
    fingerprint = operation_fingerprint(
        kind=kind, endpoint=endpoint, arguments=arguments,
        requirement=requirement, idempotency_key=idempotency_key,
    )
    guard = _idempotency_check(fingerprint, override=override, replay_success=bool(idempotency_key))
    if guard is not None:
        return json.dumps(guard)

    journal_id: int | None = None
    try:
        journal_id = ledger.journal_begin(
            fingerprint=fingerprint, idempotency_key=idempotency_key, kind=kind,
            endpoint=endpoint, cap_usdc=cap, session_id=session_id,
            budget_usdc=config.session_budget_usdc(),
        )
    except ledger.BudgetExceededError as exc:
        return json.dumps({
            "error": "session_budget_exceeded",
            "message": str(exc),
            "detail": "raise x402.session_budget_usdc to continue.",
        })
    except ledger.JournalError:
        if config.is_strict():
            return json.dumps({
                "error": "journal_unavailable",
                "message": "could not durably record the payment attempt and failure_mode "
                           "is strict; refusing the paid call.",
            })

    try:
        output, amount, tx = run()
    except StructuredToolError as exc:
        # Rich classified error — return it as-is; journal as "failed" (no money moved).
        if journal_id is not None:
            ledger.journal_finalize(journal_id, state="failed")
        return json.dumps(exc.error_dict)
    except Exception as exc:
        # "unknown" = money may have moved (timeout mid-flight); anything else errored before
        # payment, so it's safe to mark "failed" (future retries allowed).
        is_unknown = "UnknownSettlement" in type(exc).__name__
        if journal_id is not None:
            ledger.journal_finalize(journal_id, state="unknown" if is_unknown else "failed")
        return json.dumps(payment_error(exc, cap, label))

    if journal_id is not None:
        ledger.journal_finalize(
            journal_id, state="succeeded", amount_usdc=amount, tx=tx, result_json=json.dumps(output)
        )
    return json.dumps(output)
