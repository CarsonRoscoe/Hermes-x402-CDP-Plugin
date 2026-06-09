"""The payment-client seam the x402 SDK plugs into.

`x402HttpxClient` (HTTP) and `x402MCPClient` (MCP) both reach the wallet through a single
method: ``create_payment_payload(payment_required) -> PaymentPayload`` (plus a no-op
``handle_payment_response``). We implement exactly that, normalizing the full
``PaymentRequired`` and validating the returned ``PaymentPayload``. The shipped provider
signs locally via the CDP SDK; the remote Coinbase MCP branch is retained as future
provider plumbing.

Per-call budget (two gates):
1. Before contacting the signer, refuse if *every* accepted requirement exceeds the cap
   (cheapest option still unaffordable).
2. After signing, enforce that the requirement the signer actually *selected* is within the
   cap — the signer picks which ``PaymentRequirements`` to pay, so a payload is never sent
   (no settlement) if its selected amount exceeds the cap. Signing is off-chain; money only
   moves when the payload is submitted to the resource server, so refusing here is safe.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import config
from .connection import CoinbaseMcpConnection, get_connection

logger = logging.getLogger(__name__)


class PaymentExceedsCapError(Exception):
    """Raised when the (cheapest or selected) payment amount exceeds the per-call cap."""

    def __init__(self, amount_usdc: float | None, cap_usdc: float) -> None:
        super().__init__(f"payment {amount_usdc} USDC exceeds cap {cap_usdc} USDC")
        self.amount_usdc = amount_usdc
        self.min_usdc = amount_usdc  # backward-compatible alias
        self.cap_usdc = cap_usdc


class PaymentVerificationError(Exception):
    """Raised under strict mode when the selected payment amount cannot be verified."""


def _get(obj: Any, key: str):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _requirement_amount_raw(req: Any) -> Any:
    """Raw base-unit amount of a requirement, across x402 v1 and v2 shapes.

    Prefers the model's ``get_amount()`` (PaymentRequirements -> ``amount``,
    PaymentRequirementsV1 -> ``maxAmountRequired``); falls back to dict keys for
    either wire shape.
    """
    getter = getattr(req, "get_amount", None)
    if callable(getter):
        try:
            val = getter()
            if val is not None:
                return val
        except Exception:  # pragma: no cover - defensive
            pass
    for key in ("amount", "maxAmountRequired", "max_amount_required"):
        val = _get(req, key)
        if val is not None:
            return val
    return None


def _to_usdc(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return int(raw) / config.USDC_BASE_UNITS
    except (TypeError, ValueError):
        return None


def min_usdc_in_accepts(payment_required: Any) -> float | None:
    """Smallest acceptable price (USDC) across a PaymentRequired's accepts, if parseable.

    Handles both x402 v1 (``maxAmountRequired``) and v2 (``amount``) requirements.
    """
    accepts = _get(payment_required, "accepts") or []
    amounts: list[float] = []
    for r in accepts:
        usdc = _to_usdc(_requirement_amount_raw(r))
        if usdc is not None:
            amounts.append(usdc)
    return min(amounts) if amounts else None


def selected_usdc(payload: Any, payment_required: Any = None) -> float | None:
    """USDC amount of the requirement the signer selected.

    v2: read it directly from ``PaymentPayload.accepted``.
    v1: ``PaymentPayloadV1`` carries no amount, so match the payload's scheme+network
    back to the original ``accepts`` and take the highest matching price (conservative,
    so the per-call cap fails closed rather than under-counting).
    """
    accepted = _get(payload, "accepted")
    if accepted is not None:
        return _to_usdc(_requirement_amount_raw(accepted))

    if payment_required is not None:
        scheme = _get(payload, "scheme")
        network = _get(payload, "network")
        accepts = _get(payment_required, "accepts") or []
        amounts = [
            usdc
            for r in accepts
            if _get(r, "scheme") == scheme and _get(r, "network") == network
            for usdc in (_to_usdc(_requirement_amount_raw(r)),)
            if usdc is not None
        ]
        if amounts:
            return max(amounts)
    return None


class CoinbaseMcpPaymentClient:
    """Duck-typed x402 payment client backed by the active signer backend.

    Drop-in for both ``x402HttpxClient(client)`` and ``x402MCPClient(adapter, client)``.
    """

    def __init__(
        self,
        connection: CoinbaseMcpConnection | None = None,
        *,
        max_price_usdc: float | None = None,
    ) -> None:
        # The MCP connection is only used by the "coinbase_mcp" provider; the local
        # provider signs in-process via the CDP SDK. Resolve lazily so local mode never
        # spins up an MCP connection it won't use.
        self._connection = connection
        self._cap = max_price_usdc or 0.0
        #: price of the last requirement set seen, for ledger/reporting.
        self.last_min_usdc: float | None = None
        #: true only when a payment payload was successfully created/validated.
        self.last_payload_signed: bool = False

    @property
    def _conn(self) -> CoinbaseMcpConnection:
        if self._connection is None:
            self._connection = get_connection()
        return self._connection

    async def create_payment_payload(self, payment_required: Any) -> Any:
        """Sign via the active wallet provider; return a validated ``PaymentPayload``.

        Budget is checked first (no x402 import needed) so an over-cap call is refused
        before we ever contact the signer. Signing is delegated to the provider backend:
        the local CDP signer today, or the Coinbase MCP remote signer when that provider
        ships.
        """
        self.last_payload_signed = False
        self.last_min_usdc = min_usdc_in_accepts(payment_required)
        if self._cap > 0 and self.last_min_usdc is not None and self.last_min_usdc > self._cap:
            raise PaymentExceedsCapError(self.last_min_usdc, self._cap)

        # Normalize to the wire JSON shape (camelCase x402 v2 fields).
        if hasattr(payment_required, "model_dump"):
            pr_dict = payment_required.model_dump(by_alias=True)
        elif isinstance(payment_required, dict):
            pr_dict = payment_required
        else:
            from x402.schemas import PaymentRequired

            pr_dict = PaymentRequired.model_validate(payment_required).model_dump(by_alias=True)

        if config.is_local_provider():
            from ..cdp import signer as cdp_signer

            payload_dict = await cdp_signer.create_payment_payload_async(pr_dict)
        else:
            # FUTURE WORK: remote Coinbase MCP provider (coinbase_mcp/connection.py).
            # Unreachable today — WALLET_PROVIDERS = ("local",) ensures is_local_provider()
            # is always True. This branch is retained for the future remote-signer transition.
            result = await self._conn.call_tool(
                "create_payment_payload", {"payment_required": pr_dict}
            )
            # Tool may return the PaymentPayload directly or under "payment_payload".
            payload_dict = result.get("payment_payload", result)
        # Validate against the version the signer actually returned: a v1 endpoint yields
        # a PaymentPayloadV1 (scheme/network/payload at top level, no `accepted`), a v2
        # endpoint a PaymentPayload. Default to v2 when the version is absent.
        from x402.schemas import PaymentPayload, PaymentPayloadV1

        version = payload_dict.get("x402Version") if isinstance(payload_dict, dict) else None
        if version == 1:
            payload = PaymentPayloadV1.model_validate(payload_dict)
        else:
            payload = PaymentPayload.model_validate(payload_dict)

        # Gate 2: enforce the *selected* amount against the cap before the payload is ever
        # sent. Signing is off-chain; refusing here means no settlement occurs.
        if self._cap > 0:
            selected = selected_usdc(payload, payment_required)
            if selected is not None and selected > self._cap:
                raise PaymentExceedsCapError(selected, self._cap)
            if selected is None and config.is_strict():
                raise PaymentVerificationError(
                    "signer did not expose the selected payment amount; refusing under "
                    "strict failure_mode (set x402.failure_mode: best-effort to allow)"
                )
        self.last_payload_signed = True
        return payload

    async def handle_payment_response(self, ctx: Any) -> None:
        """No-op: settlement is observed by the transport; no client-side recovery."""
        return None

    def get_extensions(self) -> list:
        """The x402 HTTP client calls this to collect registered extensions.
        We register none. Returns empty list so extension-bearing endpoints (e.g. permit2
        gas-sponsoring) still fall through to the base payment flow.
        """
        return []
