"""Register the Bazaar MCP server into Hermes's ``mcp_servers:`` config.

The agent talks to Bazaar natively (Hermes exposes its tools as ``mcp_*``). The canonical
config stays under ``x402.bazaar_mcp``; this module mirrors it into ``mcp_servers`` so Hermes
connects to it. Onboarding calls ``ensure_mcp_servers``.
"""

from __future__ import annotations

from . import config

BAZAAR_SERVER_NAME = "bazaar"


def _bazaar_entry(x402: dict) -> dict:
    section = x402.get("bazaar_mcp")
    if isinstance(section, dict) and section.get("url"):
        return {"url": str(section["url"])}
    return {"url": config.bazaar_mcp_url()}


def ensure_mcp_servers(config_dict: dict) -> list[str]:
    """Ensure ``mcp_servers`` contains Bazaar discovery/proxy entry. Returns names."""
    x402 = config_dict.get("x402") if isinstance(config_dict.get("x402"), dict) else {}
    servers = config_dict.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
        config_dict["mcp_servers"] = servers

    servers[BAZAAR_SERVER_NAME] = _bazaar_entry(x402)
    # Ensure any old remote-signer entry is removed.
    servers.pop("coinbase", None)
    return [BAZAAR_SERVER_NAME]
