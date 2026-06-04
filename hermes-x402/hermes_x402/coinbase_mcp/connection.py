"""Internal connection to the Coinbase MCP server (signing + balance/identity reads).

This is the plugin's *own* connection used for signing inside the paid tools — separate
from the Coinbase MCP that onboarding also registers in ``mcp_servers`` for the agent to
call natively. Transport + result parsing live in the shared ``mcp_client`` module.

Config: ``x402.coinbase_mcp`` in config.yaml ::

    x402:
      coinbase_mcp:
        transport: stdio            # or "remote"
        command: fake-coinbase-mcp  # stdio: executable
        args: []
        url: https://mcp.coinbase.example/mcp   # remote
        auth_token_env: COINBASE_MCP_TOKEN      # remote bearer (CAT)
"""

from __future__ import annotations

import logging
import os

from .. import config
from .._async import run_async
from ..mcp_client import open_session, result_to_dict

logger = logging.getLogger(__name__)


class CoinbaseMcpConnection:
    """A per-call MCP client to the Coinbase MCP server."""

    def __init__(self, cfg: dict | None = None) -> None:
        self._cfg = cfg or config.coinbase_mcp_config()

    @property
    def transport(self) -> str:
        return str(self._cfg.get("transport") or "stdio")

    def _session(self):
        """Open a session to the Coinbase MCP using the configured transport."""
        if self.transport == "remote":
            url = self._cfg.get("url")
            if not url:
                raise RuntimeError("x402.coinbase_mcp.url is required for remote transport")
            headers = {}
            token = os.getenv(self._cfg.get("auth_token_env") or "COINBASE_MCP_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            return open_session(url=url, headers=headers or None)
        return open_session(
            command=self._cfg.get("command") or "fake-coinbase-mcp",
            args=self._cfg.get("args") or [],
        )

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
