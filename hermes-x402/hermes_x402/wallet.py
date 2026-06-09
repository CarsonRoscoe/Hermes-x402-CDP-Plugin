"""Provider-neutral wallet reads (address + USDC balance).

Dispatches to the active wallet provider so the CLI, onboarding, and monetize code never
branch on provider internals themselves. Current implementation uses the local CDP SDK
(:mod:`hermes_x402.cdp.wallet_ops`).

Both backends degrade gracefully (return ``None``) when the wallet can't be reached, so
callers never crash on a misconfigured wallet.
"""

from __future__ import annotations

import logging

from . import config

logger = logging.getLogger(__name__)


def address() -> str | None:
    """Best-effort wallet address for the active provider (``None`` if unavailable)."""
    try:
        from .cdp import wallet_ops

        return wallet_ops.status().get("address")
    except Exception as exc:  # noqa: BLE001
        logger.debug("local cdp address read failed: %s", exc)
        return None


def usdc_balance(network: str | None = None) -> float | None:
    """Best-effort on-chain USDC balance for the active provider.

    Returns 0.0 when the wallet responds but holds no USDC (unfunded); ``None`` only when
    the read itself failed (wallet unreachable / credentials missing).
    """
    net = network or config.network()
    try:
        from .cdp import wallet_ops

        val = wallet_ops.balances(net).get("usdc")
        return float(val) if val is not None else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("local cdp balance read failed: %s", exc)
        return None
