"""Agent tools exposed by the x402 plugin.

``TOOLS`` is the single source of truth consumed by ``register(ctx)`` in the package
root. Each entry pairs a name + schema (what the LLM sees) with a handler (what runs).
Handlers take ``(args: dict, **kwargs)`` and must return a JSON string.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .. import config
from . import schemas
from .cdp_tools import (
    cdp_faucet,
    cdp_onramp,
    cdp_payments,
    cdp_transfer,
    cdp_wallet_balance,
    cdp_wallet_status,
)
from .request import x402_request
from .retry_mcp import _find_bazaar_proxy_tool, _load_mcp_servers, x402_retry_mcp_payment


def _retry_mcp_schema() -> dict:
    """Build the x402_retry_mcp_payment schema with the live Bazaar proxy tool name.

    Resolves at import time (which is registration time in Hermes) so the LLM-visible
    description reflects the operator's actual ``mcp_servers`` Bazaar server name rather
    than the hardcoded default ``mcp_bazaar_proxy_tool_call``.
    """
    proxy = _find_bazaar_proxy_tool(_load_mcp_servers()) or "mcp_bazaar_proxy_tool_call"
    return schemas.build_retry_mcp_schema(proxy)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: dict
    handler: Callable[..., str]
    emoji: str = "🪙"
    # Optional availability gate (return False to hide the tool from the model).
    check_fn: Callable[[], bool] | None = None


TOOLS: tuple[ToolSpec, ...] = (
    # Always present.
    ToolSpec("x402_request", schemas.X402_REQUEST, x402_request, emoji="🪙"),
    ToolSpec("x402_retry_mcp_payment", _retry_mcp_schema(), x402_retry_mcp_payment, emoji="🔁"),
    # Local CDP wallet tools — visible only when provider == "local" (check_fn gate).
    ToolSpec("cdp_wallet_status", schemas.CDP_WALLET_STATUS, cdp_wallet_status, emoji="👛", check_fn=config.is_local_provider),
    ToolSpec("cdp_wallet_balance", schemas.CDP_WALLET_BALANCE, cdp_wallet_balance, emoji="💰", check_fn=config.is_local_provider),
    ToolSpec("cdp_faucet", schemas.CDP_FAUCET, cdp_faucet, emoji="🚰", check_fn=config.is_local_provider),
    ToolSpec("cdp_onramp", schemas.CDP_ONRAMP, cdp_onramp, emoji="🏧", check_fn=config.is_local_provider),
    ToolSpec("cdp_transfer", schemas.CDP_TRANSFER, cdp_transfer, emoji="📤", check_fn=config.is_local_provider),
    ToolSpec("cdp_payments", schemas.CDP_PAYMENTS, cdp_payments, emoji="🧾", check_fn=config.is_local_provider),
)

__all__ = ["ToolSpec", "TOOLS"]
