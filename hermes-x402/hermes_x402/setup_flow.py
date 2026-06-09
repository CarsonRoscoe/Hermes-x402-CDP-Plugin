"""Onboarding flow — the seam shared by `hermes x402 init` and `hermes setup --coinbase`.

``run_x402_onboarding(config_dict)`` is the single entry point the upstream Hermes PR
calls from its ``hermes setup --coinbase`` flag (mirroring how ``--portal`` calls
``_run_portal_one_shot``). All the real onboarding lives here so the upstream change stays
a thin flag + delegation.

Flow:
1. Provision/read the local CDP server wallet and read the wallet address.
2. Register the Bazaar MCP server in ``mcp_servers`` so the agent can call discovery/proxy
   tools natively.
3. Show USDC balance / funding hint.
4. Persist budgets (per-call + per-session caps) under ``x402:``.

Written to be non-interactive-safe so it can run from the setup wizard.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _select_provider(current: str) -> str:
    """Return the only supported provider (``local``)."""
    return "local"


def _save_config(config_dict: dict) -> bool:
    try:
        from hermes_cli.config import save_config

        save_config(config_dict)
        return True
    except Exception as exc:
        logger.warning("hermes-x402: could not persist config (mcp_servers not saved): %s", exc)
        print(f"Warning: config could not be saved automatically ({exc}).")
        print("You may need to add mcp_servers manually to ~/.hermes/config.yaml.")
        return False


def run_x402_onboarding(config_dict: dict | None = None) -> dict:
    """Run end-to-end x402 onboarding. Returns a summary dict.

    Args:
        config_dict: the loaded Hermes config to mutate + save. When ``None`` we load it via
            Hermes's config helpers (falling back to an empty dict standalone).
    """
    from . import config as cfg
    from . import mcp_servers, wallet

    if config_dict is None:
        try:
            from hermes_cli.config import load_config

            config_dict = load_config() or {}
        except Exception:
            config_dict = {}

    summary: dict = {"steps": []}

    # 0. Choose the wallet provider and persist it before anything reads it.
    x402_section = config_dict.setdefault("x402", {})
    if not isinstance(x402_section, dict):
        x402_section = {}
        config_dict["x402"] = x402_section
    provider = _select_provider(str(x402_section.get("provider") or cfg.wallet_provider()))
    x402_section["provider"] = provider
    summary["provider"] = provider
    summary["steps"].append("provider")

    # 1. Wallet: provision the self-custodial CDP server wallet.
    missing = cfg.missing_cdp_credentials()
    if missing:
        print(
            "CDP credentials are not set: "
            + ", ".join(missing)
            + ".\n  Add them to ~/.hermes/.env, then re-run `hermes x402 init`:"
            "\n    CDP_API_KEY_ID=...\n    CDP_API_KEY_SECRET=...\n    CDP_WALLET_SECRET=..."
        )
        summary["wallet"] = {"address": None, "signer": "local", "missing_credentials": missing}
    else:
        address = wallet.address()  # provisions the CDP server wallet on first call
        summary["wallet"] = {"address": address, "signer": "local"}
        if address:
            print(f"Local CDP wallet ready: {address}")
        else:
            print("CDP wallet not reachable — check CDP credentials in ~/.hermes/.env.")
    summary["steps"].append("signer")

    # 2. Reconcile MCP servers for the local provider (Bazaar always; stale coinbase removed).
    names = mcp_servers.ensure_mcp_servers(config_dict)
    summary["mcp_servers"] = names
    summary["steps"].append("mcp_servers")
    print(f"Registered MCP servers: {', '.join(names)}")

    # 3. Balance / funding hint
    network = cfg.network()
    addr = summary.get("wallet", {}).get("address")
    try:
        balance = wallet.usdc_balance(network)
        summary["balance_usdc"] = balance
        if balance and balance > 0:
            print(f"Balance: {balance} USDC on {network}")
        elif addr:
            if provider == "local" and cfg.is_testnet(network):
                print(f"Fund it on {network}: ask the agent to call cdp_faucet (testnet).")
            elif provider == "local":
                print(f"Fund it on {network}: ask the agent to call cdp_onramp, or send USDC to {addr}")
            else:
                print(f"Fund it: send USDC on {network} to {addr}")
    except Exception as exc:
        logger.debug("balance check failed during onboarding: %s", exc)

    # 4. Budgets (idempotent defaults under x402:)
    if isinstance(x402_section, dict):
        x402_section.setdefault("max_price_usdc", cfg.max_price_usdc())
        x402_section.setdefault("session_budget_usdc", cfg.session_budget_usdc())
    summary["budgets"] = {
        "max_price_usdc": cfg.max_price_usdc(),
        "session_budget_usdc": cfg.session_budget_usdc(),
    }

    # 5. Auto-enable the plugin so Hermes loads it without a manual config edit.
    plugins = config_dict.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        plugins = {}
        config_dict["plugins"] = plugins
    enabled = plugins.setdefault("enabled", [])
    if not isinstance(enabled, list):
        enabled = []
        plugins["enabled"] = enabled
    if "hermes-x402" not in enabled:
        enabled.append("hermes-x402")
        summary["plugin_enabled"] = True
        print("Plugin 'hermes-x402' added to plugins.enabled in config.")
    else:
        summary["plugin_enabled"] = False
    summary["steps"].append("plugins")

    if _save_config(config_dict):
        summary["steps"].append("saved")

    print("Done. Try: hermes x402 status   |   hermes (chat and let it pay for tools)")
    print("If mcp_servers changed, restart Hermes so tool descriptions reflect the new names.")
    return summary
