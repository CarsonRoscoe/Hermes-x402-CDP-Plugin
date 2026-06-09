"""Payment-client seam shared by local CDP signing and future remote signer plumbing.

In this release, signing is local via ``hermes_x402.cdp.signer``. The MCP connection
objects remain for future hosted signer support and for compatibility with existing code
that imports ``coinbase_mcp.*`` paths.
"""

from __future__ import annotations

from .connection import CoinbaseMcpConnection, get_connection
from .payment_client import CoinbaseMcpPaymentClient, PaymentExceedsCapError
from .wallet import address, usdc_balance

__all__ = [
    "CoinbaseMcpConnection",
    "get_connection",
    "CoinbaseMcpPaymentClient",
    "PaymentExceedsCapError",
    "address",
    "usdc_balance",
]
