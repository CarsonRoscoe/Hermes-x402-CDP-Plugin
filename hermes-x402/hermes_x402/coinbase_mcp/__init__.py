"""Client side of the Coinbase MCP integration.

Signing is delegated to a Coinbase MCP server (OAuth'd, remote in prod; a local stdio
fake in dev). This package holds the connection to that server, the payment-client seam
the x402 SDK plugs into, and a thin read facade for balance/identity.

Nothing here holds key material — the Coinbase MCP signs on the user's behalf.
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
