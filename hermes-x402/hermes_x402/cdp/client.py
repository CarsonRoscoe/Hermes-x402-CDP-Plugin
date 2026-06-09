"""The CDP server-wallet singleton for the local provider.

``Wallet`` provisions (or reuses) a named CDP server wallet via the CDP SDK and exposes
async operations for wallet management (balance, faucet, transfer, onramp URL) plus a
cached signer for x402 EIP-3009 signing. All CDP/x402 imports are lazy so this module is
safe to import even when those SDKs aren't installed or the provider is ``coinbase_mcp``.

Credentials (from the environment or ``~/.hermes/.env``):
  CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET
Optional:
  CDP_ACCOUNT_NAME / x402.cdp_account_name (default: hermes-x402)
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from .. import config

logger = logging.getLogger(__name__)

# Token decimals for transfer amount conversion (human -> atomic units).
_TOKEN_DECIMALS = {"usdc": 6, "eth": 18, "weth": 18, "eurc": 6, "cbbtc": 8}


def _explorer_url(tx_hash: str, network: str) -> str:
    """Best-effort block-explorer URL for a tx hash on the given network."""
    if not tx_hash:
        return ""
    if network == "base-sepolia":
        return f"https://sepolia.basescan.org/tx/{tx_hash}"
    if network == "base":
        return f"https://basescan.org/tx/{tx_hash}"
    if network == "ethereum-sepolia":
        return f"https://sepolia.etherscan.io/tx/{tx_hash}"
    if network == "ethereum":
        return f"https://etherscan.io/tx/{tx_hash}"
    return tx_hash


class Wallet:
    """A process-wide handle to the CDP server wallet for the local provider."""

    def __init__(self) -> None:
        self._address: str | None = None
        self._account_name: str | None = None
        self._signer: Any = None  # x402 EthAccountSigner over a CDP EvmLocalAccount
        self._account: Any = None  # cached CDP EvmLocalAccount (reused for transfers)
        self._lock = asyncio.Lock()

    def _check_credentials(self) -> None:
        missing = config.missing_cdp_credentials()
        if missing:
            raise RuntimeError(
                "CDP credentials not set: "
                + ", ".join(missing)
                + ". Add them to ~/.hermes/.env (or the environment): "
                "CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET."
            )

    async def ensure(self) -> None:
        """Provision (once) the CDP account and build the cached x402 signer."""
        if self._signer is not None:
            return
        async with self._lock:
            if self._signer is not None:
                return
            self._check_credentials()
            from cdp import CdpClient
            from cdp.evm_local_account import EvmLocalAccount
            from x402.mechanisms.evm.signers import EthAccountSigner

            name = config.cdp_account_name()
            async with CdpClient() as cdp:
                acct = await cdp.evm.get_or_create_account(name=name)
                self._address = acct.address
                self._account_name = name
                self._account = acct
                # EvmLocalAccount builds its own sync signing client from the CDP
                # credentials, so it remains valid after the async client closes.
                self._signer = EthAccountSigner(EvmLocalAccount(acct))
            logger.info("CDP wallet ready: %s (%s)", name, self._address)

    @property
    def signer(self) -> Any:
        """The cached x402 ``EthAccountSigner`` (call ``ensure()`` first)."""
        return self._signer

    async def status(self) -> dict:
        await self.ensure()
        return {
            "provider": "local",
            "address": self._address,
            "account_name": self._account_name,
            "network": config.network(),
        }

    async def balances(self, network: str, asset: str | None = None) -> dict:
        await self.ensure()
        from cdp import CdpClient

        # Returns a generic ``balances[]`` list (per the x402 Hermes Wallet Interface) plus
        # ``eth``/``usdc`` convenience scalars for the common case.
        result: dict = {
            "network": network,
            "address": self._address,
            "eth": None,
            "usdc": None,
            "balances": [],
        }
        async with CdpClient() as cdp:
            resp = await cdp.evm.list_token_balances(address=self._address, network=network)
        items = getattr(resp, "balances", None)
        if items is None and isinstance(resp, list):
            items = resp
        for b in items or []:
            token = getattr(b, "token", None)
            symbol = getattr(token, "symbol", None) or ""
            amt = getattr(b, "amount", None)
            if amt is None:
                continue
            raw = getattr(amt, "amount", 0)
            decimals = getattr(amt, "decimals", 18)
            try:
                human = float(raw) / (10 ** decimals)
            except (TypeError, ValueError):
                human = 0.0
            result["balances"].append({
                "symbol": symbol,
                "amount": human,
                "decimals": decimals,
                "contract": getattr(token, "contract_address", None),
            })
            upper = symbol.upper()
            if upper in ("ETH", "WETH"):
                result["eth"] = human
            elif upper == "USDC":
                result["usdc"] = human
        if asset:
            wanted = asset.upper()
            result["balances"] = [e for e in result["balances"] if str(e["symbol"]).upper() == wanted]
        return result

    async def faucet(self, token: str, network: str) -> dict:
        await self.ensure()
        from cdp import CdpClient

        token = (token or "usdc").lower()
        if not config.is_testnet(network):
            raise ValueError(
                f"Faucet is testnet-only (e.g. base-sepolia), not '{network}'. "
                "Switch x402.network to a testnet or use cdp_onramp on mainnet."
            )
        async with CdpClient() as cdp:
            tx_hash = await cdp.evm.request_faucet(
                address=self._address, network=network, token=token
            )
        tx_hash = str(tx_hash)
        return {
            "tx_hash": tx_hash,
            "token": token,
            "network": network,
            "address": self._address,
            "explorer": _explorer_url(tx_hash, network),
        }

    async def transfer(self, to: str, amount: Any, token: str, network: str) -> dict:
        await self.ensure()

        token = (token or "usdc").lower()
        decimals = _TOKEN_DECIMALS.get(token, 18)
        try:
            atomic = int(Decimal(str(amount)) * (10 ** decimals))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"invalid transfer amount {amount!r}: {exc}") from exc
        if atomic <= 0:
            raise ValueError("transfer amount must be greater than zero")

        # Use the account cached during ensure() to avoid a redundant CDP API round-trip.
        tx_hash = await self._account.transfer(to=to, amount=atomic, token=token, network=network)
        tx_hash = str(tx_hash)
        return {
            "tx_hash": tx_hash,
            "to": to,
            "amount": str(amount),
            "token": token,
            "network": network,
            "explorer": _explorer_url(tx_hash, network),
        }

    async def onramp_url(
        self,
        *,
        purchase_currency: str = "USDC",
        network: str | None = None,
        amount: Any = None,
        payment_currency: str = "USD",
        country: str | None = None,
        subdivision: str | None = None,
        redirect_url: str | None = None,
    ) -> dict:
        await self.ensure()
        from cdp import CdpClient
        from cdp.openapi_client.api.onramp_api import OnrampApi
        from cdp.openapi_client.models.create_onramp_session_request import (
            CreateOnrampSessionRequest,
        )

        net = network or config.network()
        # The onramp delivers funds on a mainnet; surface a clear hint on testnets.
        if config.is_testnet(net):
            raise ValueError(
                f"Onramp buys real funds on mainnet and cannot deliver to a testnet ('{net}'). "
                "Use cdp_faucet for testnet funds, or set network to 'base'."
            )

        fields: dict = {
            "purchase_currency": purchase_currency,
            "destination_network": net,
            "destination_address": self._address,
        }
        # Quote-mode fields are only valid together; include them only when an amount is set.
        if amount is not None:
            fields["payment_amount"] = str(amount)
            fields["payment_currency"] = payment_currency
            if country:
                fields["country"] = country
            if subdivision:
                fields["subdivision"] = subdivision
        if redirect_url:
            fields["redirect_url"] = redirect_url

        async with CdpClient() as cdp:
            onramp = OnrampApi(api_client=cdp.cdp_api_client)
            req = CreateOnrampSessionRequest(**fields)
            resp = await onramp.create_onramp_session(create_onramp_session_request=req)

        session = getattr(resp, "session", None)
        url = getattr(session, "onramp_url", None)
        return {
            "onramp_url": url,
            "destination_address": self._address,
            "network": net,
            "purchase_currency": purchase_currency,
        }


# Process-wide singleton; binds its asyncio.Lock to the shared background loop on first use.
wallet = Wallet()
