"""x402 ``create_payment_payload`` backed by the local CDP server wallet.

This is the local provider's signing backend for the x402 payment client. It mirrors the
contract of the (future) Coinbase MCP ``create_payment_payload`` tool: take an x402
PaymentRequired (v1 or v2) and return a signed PaymentPayload, selecting an exact EVM
(EIP-3009) requirement and skipping Permit2.

The signing itself (EIP-712 over the CDP ``EvmLocalAccount``) is synchronous, so the async
entry point offloads it to a thread to avoid blocking the shared event loop while it is
awaited from inside the async payment client.
"""

from __future__ import annotations

import asyncio

from .._async import run_async
from .client import wallet

# Asset names we treat as USDC when ranking otherwise-equal requirements.
_USDC_NAMES = {"usdc", "usdc.e", "usd coin"}


def _exclude_permit2_policy(version: int, requirements: list) -> list:
    """Drop Permit2 requirements; this signer only does EIP-3009.

    Permit2 shares the ``exact`` scheme/network with EIP-3009 (differing only by
    ``extra.assetTransferMethod``), so scheme filtering alone won't remove it. If every
    requirement is Permit2 the SDK raises ``NoMatchingRequirementsError``.
    """
    kept = []
    for req in requirements:
        extra = req.get_extra() or {}
        if (extra or {}).get("assetTransferMethod") == "permit2":
            continue
        kept.append(req)
    return kept


def _prefer_usdc_selector(version: int, requirements: list) -> object:
    """Pick which requirement to pay: USDC-named assets first, else server order."""

    def score(req: object) -> int:
        extra = req.get_extra() or {}
        name = str((extra or {}).get("name") or "").lower()
        return 0 if name in _USDC_NAMES else 1

    return sorted(requirements, key=score)[0]


def _sign(payment_required: dict) -> dict:
    """Synchronous signing — assumes ``wallet.ensure()`` already ran."""
    from x402 import x402ClientSync
    from x402.mechanisms.evm.exact.register import register_exact_evm_client
    from x402.schemas import parse_payment_required

    # Parse by the server-declared x402Version (1 or 2) — no transformation.
    pr = parse_payment_required(payment_required)
    client = x402ClientSync(payment_requirements_selector=_prefer_usdc_selector)
    client.register_policy(_exclude_permit2_policy)
    register_exact_evm_client(client, wallet.signer)
    payload = client.create_payment_payload(pr)
    return payload.model_dump(by_alias=True)


async def create_payment_payload_async(payment_required: dict) -> dict:
    """Async entry point for the payment client (awaited on the shared loop)."""
    await wallet.ensure()
    loop = asyncio.get_running_loop()
    # Signing does a synchronous HTTP call (CDP sign endpoint); run it off the loop.
    return await loop.run_in_executor(None, _sign, payment_required)


def create_payment_payload(payment_required: dict) -> dict:
    """Synchronous entry point for standalone callers (CLI/tests)."""
    return run_async(create_payment_payload_async(payment_required))


__all__ = ["create_payment_payload", "create_payment_payload_async"]
