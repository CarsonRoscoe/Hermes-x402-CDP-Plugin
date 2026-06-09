"""Synchronous wallet-management facade over the CDP ``Wallet`` singleton.

Hermes tool handlers and CLI commands are synchronous; these helpers run the async CDP
operations on the shared background loop (:func:`hermes_x402._async.run_async`) and return
plain dicts. Used by the native ``cdp_*`` tools and the ``hermes x402`` CLI reads.
"""

from __future__ import annotations

from typing import Any

from .. import config
from .._async import run_async
from .client import wallet


def status() -> dict:
    """Wallet address, account name, network, and provider."""
    return run_async(wallet.status())


def balances(network: str | None = None, asset: str | None = None) -> dict:
    """On-chain token balances for the configured (or given) network.

    Returns all token balances plus ``eth``/``usdc`` convenience scalars; pass ``asset`` to
    filter ``balances[]`` to a single symbol.
    """
    return run_async(wallet.balances(network or config.network(), asset))


def faucet(token: str = "usdc", network: str | None = None) -> dict:
    """Request testnet funds from the CDP faucet (testnet networks only)."""
    return run_async(wallet.faucet(token, network or config.network()))


def transfer(to: str, amount: Any, token: str = "usdc", network: str | None = None) -> dict:
    """Send ``amount`` of ``token`` to ``to`` from the CDP server wallet."""
    return run_async(wallet.transfer(to, amount, token, network or config.network()))


def onramp_url(
    *,
    purchase_currency: str = "USDC",
    network: str | None = None,
    amount: Any = None,
    payment_currency: str = "USD",
    country: str | None = None,
    subdivision: str | None = None,
    redirect_url: str | None = None,
) -> dict:
    """Generate a single-use Coinbase Onramp URL to buy crypto with fiat."""
    return run_async(
        wallet.onramp_url(
            purchase_currency=purchase_currency,
            network=network,
            amount=amount,
            payment_currency=payment_currency,
            country=country,
            subdivision=subdivision,
            redirect_url=redirect_url,
        )
    )
