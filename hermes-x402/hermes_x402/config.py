"""Plugin paths and configuration.

All persistent state lives under a single ``x402/`` directory inside the active Hermes
home so it is profile-aware. We resolve the home via Hermes's ``get_hermes_home()`` when
available (never hardcode ``~/.hermes`` — that breaks profiles), and fall back to
``~/.hermes`` only when running outside Hermes (examples, tests).
"""

from __future__ import annotations

import os
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
# Used by the monetize (server) side.
CDP_FACILITATOR_URL = "https://api.cdp.coinbase.com/platform/v2/x402"

# CDP Bazaar MCP server: discovery (search_resources) + paid proxy (proxy_tool_call).
# Public, no auth, streamable HTTP.
BAZAAR_MCP_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/mcp"

# USDC has 6 decimals; amounts on the wire are integer base units.
USDC_BASE_UNITS = 1_000_000

# EVM network -> CAIP-2 id (for monetize's resource-server scheme registration).
EVM_CAIP2 = {"base": "eip155:8453", "base-sepolia": "eip155:84532"}

# Default timeout: 60s per paid operation.
DEFAULT_TIMEOUT_SECONDS = 60.0

# Wallet provider(s). Only local self-custodial CDP server wallet is selectable today.
WALLET_PROVIDERS = ("local",)

# Substrings that mark a network as a testnet (faucet-eligible; onramp-ineligible).
_TESTNET_MARKERS = ("sepolia", "hoodi", "testnet", "amoy", "mumbai", "fuji", "devnet")

# CDP credentials the local provider needs (self-custodial CDP server wallet).
CDP_CREDENTIAL_ENV = ("CDP_API_KEY_ID", "CDP_API_KEY_SECRET", "CDP_WALLET_SECRET")
DEFAULT_CDP_ACCOUNT_NAME = "hermes-x402"


def caip2(net: str | None = None) -> str:
    return EVM_CAIP2.get(net or network(), "eip155:8453")


# Defaults; each is overridable under the `x402:` section of config.yaml.
DEFAULTS = {
    # Wallet/signing provider (local self-custodial CDP server wallet).
    "provider": "local",
    # CDP server-wallet account name used by the local provider.
    "cdp_account_name": DEFAULT_CDP_ACCOUNT_NAME,
    "network": "base-sepolia",  # testnet default; change to "base" for mainnet
    "max_price_usdc": 1.0,  # per-call cap (applies to x402 payments and cdp_transfer)
    "session_budget_usdc": 10.0,  # cumulative cap per session for x402 paid calls
    # Cumulative cap on direct wallet transfers (cdp_transfer) per session.
    # Defaults to session_budget_usdc. Set 0 to disable the aggregate transfer cap
    # (not recommended on mainnet). Unlike session_budget_usdc this covers direct
    # wallet transfers rather than x402 payments.
    "session_transfer_budget_usdc": None,
    # Cumulative cap on ETH transfers per session (token units, not USD).
    # Defaults to session_transfer_budget_usdc when unset so ETH is bounded by default.
    "session_transfer_budget_eth": None,
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
    return str(plugin_config().get("network") or "base-sepolia")


def is_testnet(net: str | None = None) -> bool:
    """True when ``net`` (default: the configured network) is a testnet."""
    return any(m in str(net or network()).lower() for m in _TESTNET_MARKERS)


def normalize_provider(value: object) -> str:
    """Coerce a raw provider value to the only supported provider, ``"local"``."""
    p = str(value or "local").strip().lower()
    return p if p in WALLET_PROVIDERS else "local"


def wallet_provider() -> str:
    """Active wallet provider (currently only ``"local"``)."""
    return normalize_provider(plugin_config().get("provider"))


def is_local_provider() -> bool:
    """True when the self-custodial local CDP wallet/tools are active."""
    return wallet_provider() == "local"


def cdp_account_name() -> str:
    """CDP server-wallet account name for the local provider (config > env > default)."""
    cfg = plugin_config().get("cdp_account_name")
    return str(cfg or os.getenv("CDP_ACCOUNT_NAME") or DEFAULT_CDP_ACCOUNT_NAME)


def load_dotenv_into_env() -> None:
    """Best-effort: load ``~/.hermes/.env`` (profile-aware) into ``os.environ``.

    Idempotent and never overrides an already-set variable. Hermes normally loads this
    itself, but the local CDP provider also runs standalone (examples/tests) where the
    env may not have been inherited.
    """
    env_path = hermes_home() / ".env"
    try:
        if not env_path.is_file():
            return
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def missing_cdp_credentials() -> list[str]:
    """Return the CDP credential env vars that are not set (after loading ~/.hermes/.env)."""
    load_dotenv_into_env()
    return [k for k in CDP_CREDENTIAL_ENV if not os.getenv(k)]


def bazaar_mcp_url() -> str:
    cfg = plugin_config().get("bazaar_mcp")
    if isinstance(cfg, dict) and cfg.get("url"):
        return str(cfg["url"])
    return BAZAAR_MCP_URL


def max_price_usdc() -> float:
    return float(plugin_config().get("max_price_usdc") or 0) or 0.0


def session_budget_usdc() -> float:
    return float(plugin_config().get("session_budget_usdc") or 0) or 0.0


def session_transfer_budget_usdc() -> float:
    """Cumulative per-session cap for direct wallet transfers (cdp_transfer).

    Defaults to ``session_budget_usdc`` when not explicitly configured.
    Returns 0.0 when both values are unset (no cap).
    """
    cfg = plugin_config()
    raw = cfg.get("session_transfer_budget_usdc")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    # Fall back to the x402 session budget as a sensible default ceiling.
    return session_budget_usdc()


def session_transfer_budget_eth() -> float:
    """Cumulative per-session ETH cap for direct wallet transfers (cdp_transfer).

    Uses ETH token units (e.g. ``0.2`` means 0.2 ETH). Defaults to
    ``session_transfer_budget_usdc`` when not explicitly configured so ETH transfers have
    a finite aggregate ceiling by default.
    """
    cfg = plugin_config()
    raw = cfg.get("session_transfer_budget_eth")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return session_transfer_budget_usdc()


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
