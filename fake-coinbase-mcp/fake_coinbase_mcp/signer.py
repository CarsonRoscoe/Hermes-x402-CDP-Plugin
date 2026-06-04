"""CDP server wallet backend for the fake Coinbase MCP.

Provisions a CDP server wallet (``get_or_create_account``) and implements:
- ``create_payment_payload`` — signs an x402 PaymentRequired → PaymentPayload.
- ``usdc_balance`` / ``eth_balance`` — real on-chain balances via ``list_token_balances``.
- ``request_faucet`` — calls the CDP faucet (testnet only: base-sepolia).

Requires CDP credentials in the environment:
  CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET
Optional:
  CDP_ACCOUNT_NAME   (default: hermes-x402)
  CDP_NETWORK        (default: base-sepolia)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_CDP_ENV = ("CDP_API_KEY_ID", "CDP_API_KEY_SECRET", "CDP_WALLET_SECRET")
_DEFAULT_ACCOUNT_NAME = "hermes-x402"
_DEFAULT_NETWORK = "base-sepolia"


def _load_hermes_dotenv() -> None:
    """Read ~/.hermes/.env into os.environ so credentials are available even when the
    subprocess was spawned without inheriting them from the Hermes CLI process."""
    env_path = os.path.join(os.path.expanduser("~"), ".hermes", ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


# Load credentials as early as possible so all subsequent env-var reads see them.
_load_hermes_dotenv()


def _network() -> str:
    return os.getenv("CDP_NETWORK", _DEFAULT_NETWORK)


# Public RPC endpoints per network (no API key needed for read-only calls).
_RPC_URLS = {
    "base-sepolia": "https://sepolia.base.org",
    "base": "https://mainnet.base.org",
    "ethereum-sepolia": "https://rpc.sepolia.org",
    "ethereum": "https://cloudflare-eth.com",
}


def _rpc_url(network: str | None = None) -> str:
    return _RPC_URLS.get(network or _network(), "https://sepolia.base.org")


def _make_cdp_signer(evm_local_account: Any, address: str, network: str | None = None) -> Any:
    """Wrap a CDP EvmLocalAccount in the x402 SDK's EthAccountSigner.

    EIP-3009 signing only needs sign_typed_data; no RPC / read_contract required.
    The SDK's EthAccountSigner uses the correct eth_account API
    (domain_data / message_types / message_data) so the signatures are valid.
    """
    from x402.mechanisms.evm.signers import EthAccountSigner
    return EthAccountSigner(evm_local_account)


# Asset names we treat as USDC when ranking otherwise-equal requirements.
_USDC_NAMES = {"usdc", "usdc.e", "usd coin"}


def _exclude_permit2_policy(version: int, requirements: list) -> list:
    """Drop Permit2 requirements; this signer only does EIP-3009.

    Permit2 shares the ``exact`` scheme/network with EIP-3009 (it differs only by
    ``extra.assetTransferMethod``), so scheme-based filtering alone won't remove it —
    the SDK's exact-EVM client would otherwise route it to ``create_permit2_payload``.
    Mirrors the real Coinbase MCP, which only supports EIP-3009. If every requirement
    is Permit2, the SDK raises ``NoMatchingRequirementsError`` (all filtered out).
    """
    kept = []
    for req in requirements:
        extra = req.get_extra() or {}
        if (extra or {}).get("assetTransferMethod") == "permit2":
            continue
        kept.append(req)
    return kept


def _prefer_usdc_selector(version: int, requirements: list) -> object:
    """Pick which requirement to pay from the policy-filtered candidate list.

    Expresses only a preference — USDC-named assets first, otherwise the server's
    order. Works for both v1 and v2 requirements (both expose ``get_extra``).
    """
    def score(req: object) -> int:
        extra = req.get_extra() or {}
        name = str((extra or {}).get("name") or "").lower()
        return 0 if name in _USDC_NAMES else 1

    return sorted(requirements, key=score)[0]


class Signer:
    """CDP server wallet signer. Requires CDP credentials in the environment."""

    def __init__(self) -> None:
        self._signer: _CdpEvmSigner | None = None
        self._address: str | None = None
        self._account: Any = None  # CDP EvmAccount (for balance/faucet calls)
        self._lock = asyncio.Lock()

    def _check_credentials(self) -> None:
        missing = [k for k in _CDP_ENV if not os.getenv(k)]
        if missing:
            raise RuntimeError(
                f"CDP credentials not set: {', '.join(missing)}. "
                "Export CDP_API_KEY_ID, CDP_API_KEY_SECRET, and CDP_WALLET_SECRET."
            )

    async def _ensure(self) -> None:
        if self._signer is not None:
            return
        async with self._lock:
            if self._signer is not None:
                return
            self._check_credentials()
            from cdp import CdpClient
            from cdp.evm_local_account import EvmLocalAccount

            account_name = os.getenv("CDP_ACCOUNT_NAME", _DEFAULT_ACCOUNT_NAME)
            async with CdpClient() as cdp:
                acct = await cdp.evm.get_or_create_account(name=account_name)
                self._account_address = acct.address
                local_account = EvmLocalAccount(acct)
                self._signer = _make_cdp_signer(local_account, acct.address, _network())
                self._address = acct.address
            logger.info("CDP wallet ready: %s (%s)", account_name, self._address)

    def _run(self, coro):
        """Run a coroutine from synchronous server dispatch (stdio loop)."""
        return asyncio.run(coro)

    async def _async_address(self) -> str:
        await self._ensure()
        return self._address

    async def _async_balance(self, network: str) -> dict:
        await self._ensure()
        from cdp import CdpClient

        result = {"eth": None, "usdc": None, "network": network, "address": self._address}
        async with CdpClient() as cdp:
            resp = await cdp.evm.list_token_balances(
                address=self._address, network=network
            )
        # CDP SDK returns ListTokenBalancesResult with .balances list of EvmTokenBalance.
        # Each EvmTokenBalance has .token (EvmToken with .symbol) and .amount (EvmTokenAmount
        # with .amount in base units and .decimals).
        items = getattr(resp, "balances", None)
        if items is None and isinstance(resp, list):
            items = resp
        for b in (items or []):
            token = getattr(b, "token", None)
            symbol = (getattr(token, "symbol", None) or "").upper()
            amt_obj = getattr(b, "amount", None)
            if amt_obj is None:
                continue
            raw = getattr(amt_obj, "amount", 0)
            decimals = getattr(amt_obj, "decimals", 18)
            try:
                human = float(raw) / (10 ** decimals)
            except (TypeError, ValueError):
                human = 0.0
            if symbol in ("ETH", "WETH"):
                result["eth"] = human
            elif symbol == "USDC":
                result["usdc"] = human
        return result

    async def _async_faucet(self, token: str, network: str) -> str:
        await self._ensure()
        from cdp import CdpClient

        if "sepolia" not in network:
            raise ValueError(f"Faucet only works on testnets (e.g. base-sepolia), not '{network}'")
        async with CdpClient() as cdp:
            tx_hash = await cdp.evm.request_faucet(
                address=self._address, network=network, token=token
            )
        return str(tx_hash)

    async def _async_create_payment_payload(self, payment_required: dict) -> dict:
        await self._ensure()
        from x402 import x402ClientSync
        from x402.mechanisms.evm.exact.register import register_exact_evm_client
        from x402.schemas import parse_payment_required

        # Parse by the server-declared x402Version (1 or 2) — no transformation.
        # ``register_exact_evm_client`` registers both the v2 (eip155:*) and v1
        # (legacy network names) exact-EVM schemes; the SDK dispatches on version and
        # filters accepts[] to registered schemes. A policy drops Permit2 (which shares
        # the exact scheme), and a selector prefers USDC.
        pr = parse_payment_required(payment_required)

        client = x402ClientSync(payment_requirements_selector=_prefer_usdc_selector)
        client.register_policy(_exclude_permit2_policy)
        register_exact_evm_client(client, self._signer)
        payload = client.create_payment_payload(pr)
        return payload.model_dump(by_alias=True)

    # -- sync public API (called from the stdio server loop) -------------- #

    def address(self) -> str:
        return self._run(self._async_address())

    def balance(self, network: str | None = None) -> dict:
        return self._run(self._async_balance(network or _network()))

    def faucet(self, token: str, network: str | None = None) -> str:
        return self._run(self._async_faucet(token, network or _network()))

    def create_payment_payload(self, payment_required: dict) -> dict:
        return self._run(self._async_create_payment_payload(payment_required))
