"""Register the Coinbase + Bazaar MCP servers into Hermes's ``mcp_servers:`` config.

The agent talks to both servers *natively* (Hermes exposes their tools as ``mcp_*``). The
canonical config stays under ``x402.coinbase_mcp`` / ``x402.bazaar_mcp`` (the plugin's own
signing connection reads ``x402.coinbase_mcp``); this module mirrors those into the
``mcp_servers`` section so Hermes connects to them. Onboarding calls ``ensure_mcp_servers``.
"""

from __future__ import annotations

from . import config

COINBASE_SERVER_NAME = "coinbase"
BAZAAR_SERVER_NAME = "bazaar"


def _coinbase_entry(x402: dict) -> dict:
    """Translate the ``coinbase_mcp`` config into an mcp_servers entry (remote only).

    The remote Coinbase MCP is Coming Soon. This entry is written only when
    ``provider == "coinbase_mcp"`` so it never appears in local mode.
    """
    section = x402.get("coinbase_mcp") or {}
    url = section.get("url") or ""
    entry: dict = {"url": url}
    token_env = section.get("auth_token_env") or "COINBASE_MCP_TOKEN"
    # Hermes expands ${ENV} in headers; keep the bearer out of the committed file.
    entry["headers"] = {"Authorization": "Bearer ${%s}" % token_env}
    return entry


def _bazaar_entry(x402: dict) -> dict:
    section = x402.get("bazaar_mcp")
    if isinstance(section, dict) and section.get("url"):
        return {"url": str(section["url"])}
    return {"url": config.bazaar_mcp_url()}


def ensure_mcp_servers(config_dict: dict) -> list[str]:
    """Reconcile the ``mcp_servers`` entries to the active wallet provider. Returns names.

    Exactly one wallet surface exists at a time:
    - ``provider == "coinbase_mcp"``: add/refresh the ``coinbase`` MCP entry (the agent calls
      ``mcp_coinbase_*``).
    - ``provider == "local"`` (default): remove any stale ``coinbase`` entry — signing and
      wallet management run in-process via the native ``cdp_*`` tools instead.

    The ``bazaar`` discovery/proxy MCP is registered in both modes. Sources the canonical
    ``x402.*`` config from the passed ``config_dict`` (authoritative, in-memory). Mutates
    ``config_dict`` in place.
    """
    x402 = config_dict.get("x402") if isinstance(config_dict.get("x402"), dict) else {}
    servers = config_dict.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
        config_dict["mcp_servers"] = servers

    provider = config.normalize_provider(x402.get("provider"))
    servers[BAZAAR_SERVER_NAME] = _bazaar_entry(x402)

    if provider == "coinbase_mcp":
        servers[COINBASE_SERVER_NAME] = _coinbase_entry(x402)
        return [COINBASE_SERVER_NAME, BAZAAR_SERVER_NAME]

    # Local provider: ensure the remote signer MCP is not also present.
    servers.pop(COINBASE_SERVER_NAME, None)
    return [BAZAAR_SERVER_NAME]
