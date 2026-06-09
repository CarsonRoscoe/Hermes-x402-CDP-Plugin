"""`hermes x402 status` and the `/x402` slash summary.

Reports the signer connection and wallet address + USDC balance. ``status_summary``
returns a JSON string (used by the slash command); ``status_command`` prints a
human-friendly version for the CLI.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _gather() -> dict:
    from .. import config, wallet

    provider = config.wallet_provider()
    out: dict = {"network": config.network(), "provider": provider}
    out["signer"] = "local CDP wallet" if provider == "local" else "coinbase-mcp (coming soon)"
    try:
        out["address"] = wallet.address()
        out["usdc_balance"] = wallet.usdc_balance(config.network())
    except Exception as exc:
        out["wallet_error"] = str(exc)
    return out


def status_summary(raw_args: str = "") -> str:
    """JSON status string for the /x402 slash command."""
    return json.dumps(_gather())


def status_command(args) -> int:
    s = _gather()
    print("x402 status")
    print(f"  provider: {s.get('provider', '?')}")
    print(f"  signer:   {s.get('signer', '?')}")
    print(f"  wallet:   {s.get('address') or '(unavailable)'}")
    bal = s.get("usdc_balance")
    print(f"  balance:  {bal if bal is not None else '(unknown)'} USDC on {s.get('network')}")
    return 0
