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
    """Translate the ``coinbase_mcp`` config into an mcp_servers entry.

    Merges the passed ``x402`` section (authoritative, in-memory) over ``DEFAULTS`` so the
    mirror never disagrees with what onboarding is about to persist.
    """
    cb = dict(config.DEFAULTS["coinbase_mcp"])
    section = x402.get("coinbase_mcp")
    if isinstance(section, dict):
        cb.update(section)
    if (cb.get("transport") or "stdio") == "remote":
        entry: dict = {"url": cb.get("url") or ""}
        token_env = cb.get("auth_token_env")
        if token_env:
            # Hermes expands ${ENV} in headers; keep the bearer out of the committed file.
            entry["headers"] = {"Authorization": "Bearer ${%s}" % token_env}
        return entry
    return {"command": cb.get("command") or "fake-coinbase-mcp", "args": list(cb.get("args") or [])}


def _bazaar_entry(x402: dict) -> dict:
    section = x402.get("bazaar_mcp")
    if isinstance(section, dict) and section.get("url"):
        return {"url": str(section["url"])}
    return {"url": config.bazaar_mcp_url()}


def ensure_mcp_servers(config_dict: dict) -> list[str]:
    """Add/refresh the coinbase + bazaar entries under ``mcp_servers``. Returns the names.

    Sources the canonical ``x402.*`` config from the passed ``config_dict`` (authoritative,
    in-memory) so the mirror matches what onboarding persists, falling back to disk-derived
    defaults only when the section is absent. Mutates ``config_dict`` in place.
    """
    x402 = config_dict.get("x402") if isinstance(config_dict.get("x402"), dict) else {}
    servers = config_dict.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
        config_dict["mcp_servers"] = servers
    servers[COINBASE_SERVER_NAME] = _coinbase_entry(x402)
    servers[BAZAAR_SERVER_NAME] = _bazaar_entry(x402)
    return [COINBASE_SERVER_NAME, BAZAAR_SERVER_NAME]
