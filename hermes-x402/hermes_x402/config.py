"""Plugin paths and configuration.

All persistent state lives under a single ``x402/`` directory inside the active Hermes
home so it is profile-aware. We resolve the home via Hermes's ``get_hermes_home()`` when
available (never hardcode ``~/.hermes`` — that breaks profiles), and fall back to
``~/.hermes`` only when running outside Hermes (examples, tests).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def hermes_home() -> Path:
    """Return the active Hermes home directory (profile-aware when in Hermes)."""
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        # Standalone (examples/tests): honor HERMES_HOME if set, else ~/.hermes.
        env = os.environ.get("HERMES_HOME")
        return Path(env) if env else Path.home() / ".hermes"


def data_dir() -> Path:
    """Return (without creating) the plugin's data directory."""
    return hermes_home() / "x402"


def ensure_data_dir() -> Path:
    """Create and return the plugin's data directory."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def ledger_path() -> Path:
    """Path to the sqlite spend/payment ledger."""
    return data_dir() / "ledger.sqlite"


# CDP x402 facilitator (production). Testnet is https://x402.org/facilitator (no auth).
# Used by the monetize (server) side; the client signs via the Coinbase MCP.
CDP_FACILITATOR_URL = "https://api.cdp.coinbase.com/platform/v2/x402"

# CDP Bazaar MCP server: discovery (search_resources) + paid proxy (proxy_tool_call).
# Public, no auth, streamable HTTP. We connect to it on startup in addition to the
# Coinbase MCP signer.
BAZAAR_MCP_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/mcp"

# USDC has 6 decimals; amounts on the wire are integer base units.
USDC_BASE_UNITS = 1_000_000

# EVM network -> CAIP-2 id (for monetize's resource-server scheme registration).
EVM_CAIP2 = {"base": "eip155:8453", "base-sepolia": "eip155:84532"}

# Default timeout: 60s per paid operation.
DEFAULT_TIMEOUT_SECONDS = 60.0


def _default_signer_command() -> str:
    """Resolve the fake-coinbase-mcp binary at config-read time.

    Prefers the binary installed alongside this package (same venv). This matters when the
    plugin runs inside Hermes: the Hermes venv is not necessarily on PATH, but the binary
    was installed into the same venv as the plugin itself.
    """
    # Same directory as the Python executable Hermes is using.
    venv_bin = Path(sys.executable).parent / "fake-coinbase-mcp"
    if venv_bin.exists():
        return str(venv_bin)
    return "fake-coinbase-mcp"  # fall back to PATH for standalone installs


def caip2(net: str | None = None) -> str:
    return EVM_CAIP2.get(net or network(), "eip155:8453")


# Defaults; each is overridable under the `x402:` section of config.yaml.
DEFAULTS = {
    "network": "base-sepolia",  # testnet default; change to "base" for mainnet
    "max_price_usdc": 1.0,  # per-call cap
    "session_budget_usdc": 10.0,  # cumulative cap per session
    # Failure posture for money guards. "strict" (default) fails closed: a paid call is
    # refused when a guard (budget, settlement) cannot be verified. "best-effort" prefers
    # availability and allows the call when a guard errors.
    "failure_mode": "strict",
    # Per-operation network timeout (seconds) for paid HTTP / MCP calls.
    "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
    # When False (default), x402_retry_mcp_payment ignores any agent-supplied
    # payment_required and always re-probes the upstream server in-process, so a forged
    # requirement cannot redirect a payment. Set True only if you trust the caller.
    "trust_supplied_payment_required": False,
    # Connection to the Coinbase MCP that signs x402 payloads.
    "coinbase_mcp": {
        "transport": "stdio",  # "stdio" (dev fake) | "remote" (hosted, OAuth)
        "command": "",         # resolved at runtime by _default_signer_command()
        "args": [],
        "url": "",  # remote: HTTP/SSE endpoint
        "auth_token_env": "COINBASE_MCP_TOKEN",  # remote: env var holding the OAuth/CAT bearer
    },
    # CDP Bazaar MCP (discovery + paid proxy). No auth; streamable HTTP.
    "bazaar_mcp": {
        "url": BAZAAR_MCP_URL,
    },
}


def plugin_config() -> dict:
    """Return the merged ``x402:`` config section (defaults + user config.yaml).

    Reads via Hermes's loader when available; falls back to defaults so the plugin and
    examples work standalone. Never raises.
    """
    cfg = {k: v for k, v in DEFAULTS.items()}
    try:
        from hermes_cli.config import load_config

        section = (load_config() or {}).get("x402") or {}
        if isinstance(section, dict):
            cfg.update(section)
    except Exception:
        pass
    return cfg


def network() -> str:
    return str(plugin_config().get("network") or "base")


def coinbase_mcp_config() -> dict:
    cfg = plugin_config().get("coinbase_mcp")
    base = dict(DEFAULTS["coinbase_mcp"])
    if isinstance(cfg, dict):
        base.update(cfg)
    # If command is empty/unset, resolve the venv-local binary at call time.
    if not base.get("command"):
        base["command"] = _default_signer_command()
    return base


def bazaar_mcp_url() -> str:
    cfg = plugin_config().get("bazaar_mcp")
    if isinstance(cfg, dict) and cfg.get("url"):
        return str(cfg["url"])
    return BAZAAR_MCP_URL


def max_price_usdc() -> float:
    return float(plugin_config().get("max_price_usdc") or 0) or 0.0


def session_budget_usdc() -> float:
    return float(plugin_config().get("session_budget_usdc") or 0) or 0.0


def failure_mode() -> str:
    """Money-guard posture: ``"strict"`` (fail closed) or ``"best-effort"`` (fail open)."""
    mode = str(plugin_config().get("failure_mode") or "strict").strip().lower()
    return mode if mode in ("strict", "best-effort") else "strict"


def is_strict() -> bool:
    """True when paid calls must fail closed if a money guard cannot be verified."""
    return failure_mode() == "strict"


def timeout_seconds() -> float:
    """Per-operation network timeout for paid HTTP / MCP calls (0 => no timeout)."""
    try:
        val = float(plugin_config().get("timeout_seconds") or 0)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return val if val > 0 else DEFAULT_TIMEOUT_SECONDS


def trust_supplied_payment_required() -> bool:
    """Whether x402_retry_mcp_payment may pay an agent-supplied requirement without probing."""
    return bool(plugin_config().get("trust_supplied_payment_required"))
