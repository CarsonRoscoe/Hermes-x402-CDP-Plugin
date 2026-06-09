"""FUTURE WORK — NOT ACTIVE IN THE CURRENT RELEASE.

Wallet reads (address + USDC balance) for the remote Coinbase MCP provider. This module
is unreachable in the current release because ``WALLET_PROVIDERS = ("local",)`` forces
``normalize_provider()`` to always return ``"local"``.

The provider-neutral entry point is :mod:`hermes_x402.wallet`, which dispatches only to
the local CDP provider today.
"""

from __future__ import annotations

import logging
from typing import Any

from .connection import get_connection

logger = logging.getLogger(__name__)


def _call(name: str, args: dict | None = None) -> dict | None:
    try:
        return get_connection().call_tool_sync(name, args or {})
    except Exception as exc:
        logger.debug("coinbase mcp read %s failed: %s", name, exc)
        return None


def address() -> str | None:
    """Best-effort wallet address via ``coinbase_status`` (then ``coinbase_balance``)."""
    status = _call("coinbase_status") or {}
    addr = _pluck(status, ("address", "wallet_address", "wallet"))
    if addr:
        return addr
    bal = _call("coinbase_balance") or {}
    return _pluck(bal, ("address", "wallet_address"))


def usdc_balance(network: str = "base") -> float | None:
    """Best-effort on-chain USDC balance via ``coinbase_balance``.

    Returns 0.0 when the tool responds but the wallet has no USDC yet (unfunded). Returns
    None only when the tool call itself failed (signer unreachable).
    """
    result = _call("coinbase_balance", {"network": network})
    if result is None:
        return None  # tool call failed — genuinely unknown
    bal = result if isinstance(result, dict) else {}
    val = _pluck(bal, ("usdc", "usdc_balance", "balance"))
    if val is None:
        return 0.0  # tool responded but no USDC entry — wallet is empty/unfunded
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _pluck(obj: Any, keys: tuple[str, ...]):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if obj.get(k) is not None:
            return obj[k]
    return None
