"""FUTURE WORK — NOT ACTIVE IN THE CURRENT RELEASE.

This module contains plumbing for the remote Coinbase MCP signing provider, which is not
yet available. ``WALLET_PROVIDERS = ("local",)`` in ``config.py`` means
``normalize_provider()`` always returns ``"local"``, so the ``coinbase_mcp`` provider
branch in ``payment_client.py`` is unreachable. This file exists only to pre-wire the
seam so the transition requires minimal changes when the remote signer ships.

No code in this module is executed during normal plugin operation. Any import of
symbols from here outside of tests or future-provider bringup is a bug.

When the remote Coinbase MCP ships, the config block will look like::

    x402:
      provider: coinbase_mcp         # switch from "local" when available
      coinbase_mcp:
        transport: remote
        url: https://mcp.coinbase.com/mcp
        auth_token_env: COINBASE_MCP_TOKEN   # OAuth/CAT bearer
"""

from __future__ import annotations

import logging
import os

from .. import config
from .._async import run_async
from ..mcp_client import open_session, result_to_dict

logger = logging.getLogger(__name__)


class CoinbaseMcpConnection:
    """A per-call MCP client to the remote Coinbase MCP server."""

    def __init__(self, cfg: dict | None = None) -> None:
        raw = cfg or (plugin_cfg := config.plugin_config().get("coinbase_mcp") or {})
        self._cfg = raw if cfg is not None else (plugin_cfg or {})

    @property
    def transport(self) -> str:
        return str(self._cfg.get("transport") or "remote")

    def _session(self):
        """Open a session to the remote Coinbase MCP."""
        url = self._cfg.get("url")
        if not url:
            raise RuntimeError(
                "Coinbase MCP provider is Coming Soon. "
                "Use provider: local (the default) for self-custodial CDP wallet tools."
            )
        headers = {}
        token = os.getenv(self._cfg.get("auth_token_env") or "COINBASE_MCP_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return open_session(url=url, headers=headers or None)

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call a Coinbase MCP tool and return its JSON result. Await directly."""
        from ..mcp_client import with_timeout

        async with self._session() as session:
            result = await with_timeout(session.call_tool(name=name, arguments=arguments or {}))
            return result_to_dict(result)

    def call_tool_sync(self, name: str, arguments: dict | None = None) -> dict:
        """Sync convenience for CLI/status paths (hops onto the shared loop)."""
        return run_async(self.call_tool(name, arguments))


_cached: CoinbaseMcpConnection | None = None


def get_connection(refresh: bool = False) -> CoinbaseMcpConnection:
    """Return the process-wide Coinbase MCP connection (config-driven)."""
    global _cached
    if _cached is None or refresh:
        _cached = CoinbaseMcpConnection()
    return _cached
