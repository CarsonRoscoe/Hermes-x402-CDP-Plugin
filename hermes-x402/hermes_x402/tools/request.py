"""x402_request — make an x402-paying HTTP request.

Uses the x402 async HTTP client (``x402HttpxClient``), whose transport transparently
handles the ``402 -> sign -> retry`` flow. Signing is delegated to the Coinbase MCP via
``CoinbaseMcpPaymentClient`` — no key in the plugin. The per-call cap is enforced in the
payment client (it refuses before signing if the cheapest accepted requirement exceeds the
cap); the session budget is enforced by the ``pre_tool_call`` gate.

Returns a JSON string: ``{status, body, payment: {...} | null}`` on success, or
``{error, detail, ...}`` with actionable context on every failure path.
"""

from __future__ import annotations

import json
import logging

from .. import config
from .._async import run_async
from ._paid import (
    StructuredToolError,
    decode_x402_header,
    effective_cap,
    extract_server_error,
    record_paid_call,
    run_journaled,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Response parsing helpers
# --------------------------------------------------------------------------- #

def _decode_payment_response(headers) -> dict | None:
    """Decode the x402 PAYMENT-RESPONSE header (base64 JSON SettleResponse), if present.

    Checks V2 name first (``PAYMENT-RESPONSE``), then the V1 legacy name
    (``X-PAYMENT-RESPONSE``).
    """
    for name in ("PAYMENT-RESPONSE", "X-PAYMENT-RESPONSE"):
        raw = headers.get(name)
        if raw:
            return decode_x402_header(raw)
    return None


def _decode_payment_required(headers) -> dict | None:
    """Decode the x402 PAYMENT-REQUIRED header so we can surface it on failed payment.

    Checks V2 name first (``PAYMENT-REQUIRED``), then the V1 legacy name
    (``X-PAYMENT-REQUIRED``).
    """
    for name in ("PAYMENT-REQUIRED", "X-PAYMENT-REQUIRED"):
        raw = headers.get(name)
        if raw:
            return decode_x402_header(raw)
    return None


def _classify_402(exc: "_Payment402Error") -> dict:
    """Build an actionable error dict for a 402 result, with full raw server context."""
    status_code = exc.status
    resp_text = exc.text or ""
    resp_headers = exc.headers or {}
    payment_required = exc.payment_required
    signing_occurred = exc.signing_occurred
    last_min_usdc = exc.last_min_usdc

    body_parsed: dict | None = None
    try:
        body_parsed = json.loads(resp_text) if resp_text.strip() else None
    except Exception:
        pass

    # Extract any error detail the server sent back.
    server_error = extract_server_error(body_parsed)
    # Include the raw body so the agent can see exactly what the server said.
    raw_body = resp_text[:4000] if resp_text.strip() and resp_text.strip() != "{}" else None

    # Relevant response headers (exclude noisy ones).
    relevant_headers = {
        k: v for k, v in resp_headers.items()
        if k.lower() in (
            "content-type", "x-payment-response", "payment-response",
            "payment-required", "x-error", "x-reason", "cf-ray",
        )
    }

    if not signing_occurred:
        # We never signed — either not an x402 endpoint or PAYMENT-REQUIRED was missing/unparseable.
        return {
            "error": "payment_not_attempted_402",
            "detail": (
                "Server returned 402 but the plugin did not sign a payment. "
                "This may not be an x402 endpoint, or the PAYMENT-REQUIRED header was absent or unparseable."
            ),
            "status": status_code,
            "server_error": server_error,
            "raw_body": raw_body,
            "response_headers": relevant_headers,
        }

    # Signing occurred but the server still returned 402 — facilitator rejected the payment.
    # Build payment-requirement summary from either the 402 headers or last_min_usdc.
    req_summary: dict = {}
    if payment_required:
        accepts = payment_required.get("accepts") or []
        networks = list({r.get("network") for r in accepts if r.get("network")})
        schemes = list({r.get("scheme") for r in accepts if r.get("scheme")})
        min_amount_raw = min((int(r["amount"]) for r in accepts if r.get("amount")), default=None)
        req_summary = {
            "networks": networks,
            "schemes": schemes,
            "required_usdc": round(min_amount_raw / config.USDC_BASE_UNITS, 6) if min_amount_raw else last_min_usdc,
            "resource": payment_required.get("resource"),
        }
    elif last_min_usdc is not None:
        req_summary = {"required_usdc": last_min_usdc}

    is_insufficient = (
        "insufficient" in str(server_error).lower()
        or "insufficient" in resp_text.lower()
        or "balance" in str(server_error).lower()
    )
    if is_insufficient:
        hint = (
                "Insufficient USDC balance. Fund the wallet with testnet USDC (e.g. via "
                "`cdp_faucet`) or with mainnet USDC via `cdp_onramp`, then retry."
            )
    elif server_error:
        hint = f"Facilitator rejected with: {server_error}"
    else:
        hint = (
            "The facilitator rejected the signed payment. Common causes: "
            "insufficient USDC balance, expired nonce, or mismatched Permit2 allowance. "
            "Check wallet balance and retry."
        )

    return {
        "error": "payment_rejected_402",
        "hint": hint,
        "status": status_code,
        "signing_occurred": True,
        "signed_amount_usdc": last_min_usdc,
        "server_error": server_error,
        "raw_body": raw_body,
        "response_headers": relevant_headers,
        **req_summary,
    }


# --------------------------------------------------------------------------- #
# Core fetch
# --------------------------------------------------------------------------- #

async def _do_fetch(url, method, headers, body, cap_usdc):
    """Execute the paid HTTP call. Returns (status, text, payment, price_usdc) on success.
    Raises with a clear, classified exception on every failure path.
    """
    from x402.http.clients.httpx import PaymentError, x402HttpxClient

    from ..coinbase_mcp.payment_client import CoinbaseMcpPaymentClient

    payment_client = CoinbaseMcpPaymentClient(max_price_usdc=cap_usdc)

    try:
        async with x402HttpxClient(payment_client, timeout=config.timeout_seconds()) as http:
            resp = await http.request(method, url, headers=headers or None, content=body)
    except PaymentError as exc:
        # The SDK wraps signing/transport errors as PaymentError("Failed to handle payment: ...").
        # Unwrap the inner cause for a more useful message.
        inner = exc.__cause__ or exc
        inner_msg = str(inner)
        raise _PaymentSigningError(
            f"Payment signing failed: {inner_msg}",
            inner=inner_msg,
            signing_occurred=payment_client.last_min_usdc is not None,
        ) from exc

    payment = _decode_payment_response(resp.headers)

    if resp.status_code == 402:
        # Use the PAYMENT-REQUIRED from the response headers (may be absent on the retry 402).
        payment_required = _decode_payment_required(resp.headers)
        # Whether the SDK actually signed and retried (last_min_usdc is set after signing).
        signing_occurred = payment_client.last_min_usdc is not None
        raise _Payment402Error(
            resp.status_code, resp.text, dict(resp.headers),
            payment_required, signing_occurred, payment_client.last_min_usdc,
        )

    return resp.status_code, resp.text, payment, payment_client.last_min_usdc


class _PaymentSigningError(StructuredToolError):
    def __init__(self, msg, inner="", signing_occurred=False):
        super().__init__({
            "error": "payment_signing_failed",
            "detail": msg,
            "inner": inner,
            "signing_occurred": signing_occurred,
            "hint": (
                "Check that the Coinbase MCP signer is running and CDP credentials are set. "
                "Run `hermes x402 status` to confirm the signer is connected."
            ),
        })
        self.inner = inner


class _Payment402Error(Exception):
    def __init__(self, status, text, headers, payment_required, signing_occurred=False, last_min_usdc=None):
        super().__init__("402 after payment attempt")
        self.status = status
        self.text = text
        self.headers = headers
        self.payment_required = payment_required
        self.signing_occurred = signing_occurred
        self.last_min_usdc = last_min_usdc


# --------------------------------------------------------------------------- #
# Tool handler
# --------------------------------------------------------------------------- #

def x402_request(args: dict, **kwargs) -> str:
    """Handler for the ``x402_request`` tool. Always returns a JSON string."""
    args = args or {}
    url = args.get("url", "")
    if not url:
        return json.dumps({"error": "url is required"})

    method = (args.get("method") or "GET").upper()
    headers = args.get("headers") or {}
    body = args.get("body")
    cap = effective_cap(args.get("max_price_usdc"))
    session_id = kwargs.get("task_id")

    def run():
        try:
            status, text, payment, price = run_async(_do_fetch(url, method, headers, body, cap))
        except _Payment402Error as exc:
            raise _Structured402(_classify_402(exc)) from exc

        tx = payment.get("transaction") if isinstance(payment, dict) else None
        amount = price if price is not None else (_amount_from_payment(payment) if payment else 0.0)
        if payment:
            record_paid_call(
                kind="http", amount_usdc=amount, endpoint=url, transaction=tx, task_id=session_id
            )
        out = {"status": status, "body": text[:50_000], "payment": payment}
        return out, (amount if payment else None), tx

    return run_journaled(
        kind="http",
        endpoint=url,
        arguments={"method": method, "body": body},
        idempotency_key=args.get("idempotency_key"),
        override=bool(args.get("override")),
        cap=cap,
        label="x402_request",
        session_id=session_id,
        run=run,
    )


class _Structured402(StructuredToolError):
    pass


def _amount_from_payment(payment: dict) -> float:
    """Best-effort USDC amount from a settle response (0.0 if not present).

    The x402 spec mandates that amounts on the wire are integer base units (USDC has 6
    decimals). ``amountUsdc`` / ``amount_usdc`` are convenience human-unit aliases emitted
    by some facilitator implementations — they are left as-is. All other keys are treated
    as base units and divided by ``USDC_BASE_UNITS``.
    """
    if not isinstance(payment, dict):
        return 0.0
    # Human-unit convenience keys emitted by some facilitators — no conversion needed.
    for key in ("amountUsdc", "amount_usdc"):
        if key in payment:
            try:
                return float(payment[key])
            except (TypeError, ValueError):
                pass
    # Standard wire keys carry integer base units.
    for key in ("amount", "value"):
        if key in payment:
            try:
                return int(payment[key]) / config.USDC_BASE_UNITS
            except (TypeError, ValueError):
                pass
    return 0.0
