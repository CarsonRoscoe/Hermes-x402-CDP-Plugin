"""Agent tools exposed by the x402 plugin.

``TOOLS`` is the single source of truth consumed by ``register(ctx)`` in the package
root. Each entry pairs a name + schema (what the LLM sees) with a handler (what runs).
Handlers take ``(args: dict, **kwargs)`` and must return a JSON string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import schemas
from .request import x402_request
from .retry_mcp import x402_retry_mcp_payment


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: dict
    handler: Callable[..., str]
    emoji: str = "🪙"
    # Optional availability gate (return False to hide the tool from the model).
    check_fn: Optional[Callable[[], bool]] = None


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("x402_request", schemas.X402_REQUEST, x402_request, emoji="🪙"),
    ToolSpec("x402_retry_mcp_payment", schemas.X402_RETRY_MCP_PAYMENT, x402_retry_mcp_payment, emoji="🔁"),
)

__all__ = ["ToolSpec", "TOOLS"]
