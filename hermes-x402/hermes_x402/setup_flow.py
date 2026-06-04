"""Onboarding flow — the seam shared by `hermes x402 init` and `hermes setup --coinbase`.

``run_x402_onboarding(config_dict)`` is the single entry point the upstream Hermes PR
calls from its ``hermes setup --coinbase`` flag (mirroring how ``--portal`` calls
``_run_portal_one_shot``). All the real onboarding lives here so the upstream change stays
a thin flag + delegation.

Flow:
1. Connect to the Coinbase MCP signer (dev: local fake over stdio; prod: remote + OAuth)
   and read the wallet address.
2. Register the Coinbase + Bazaar MCP servers in ``mcp_servers`` so the agent can call them
   natively (search/discovery via Bazaar; signing reads via Coinbase).
3. Show USDC balance / funding hint.
4. Persist budgets (per-call + per-session caps) under ``x402:``.

Written to be non-interactive-safe so it can run from the setup wizard. The OAuth handshake
for the remote Coinbase MCP is performed by the host (or a future flag); here we only verify
the connection works by reading the wallet address.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
    from . import mcp_servers
    from .coinbase_mcp import wallet

    if config_dict is None:
        try:
            from hermes_cli.config import load_config

            config_dict = load_config() or {}
        except Exception:
            config_dict = {}

    summary: dict = {"steps": []}

    # 1. Signer connection (Coinbase MCP)
    transport = cfg.coinbase_mcp_config().get("transport")
    address = wallet.address()
    summary["wallet"] = {"address": address, "signer": f"coinbase-mcp:{transport}"}
    summary["steps"].append("signer")
    if address:
        print(f"Connected to Coinbase MCP ({transport}); wallet: {address}")
    else:
        print(
            f"Coinbase MCP ({transport}) not reachable yet. For dev, install + point at "
            "fake-coinbase-mcp; for prod, complete the OAuth connection."
        )

    # 2. Register the MCP servers the agent calls natively.
    names = mcp_servers.ensure_mcp_servers(config_dict)
    summary["mcp_servers"] = names
    summary["steps"].append("mcp_servers")
    print(f"Registered MCP servers: {', '.join(names)}")

    # 3. Balance / funding hint
    network = cfg.network()
    try:
        balance = wallet.usdc_balance(network)
        summary["balance_usdc"] = balance
        if balance and balance > 0:
            print(f"Balance: {balance} USDC on {network}")
        elif address:
            print(f"Fund it: send USDC on {network} to {address}")
    except Exception as exc:
        logger.debug("balance check failed during onboarding: %s", exc)

    # 4. Budgets (idempotent defaults under x402:)
    x402_section = config_dict.setdefault("x402", {})
    if isinstance(x402_section, dict):
        x402_section.setdefault("max_price_usdc", cfg.max_price_usdc())
        x402_section.setdefault("session_budget_usdc", cfg.session_budget_usdc())
    summary["budgets"] = {
        "max_price_usdc": cfg.max_price_usdc(),
        "session_budget_usdc": cfg.session_budget_usdc(),
    }

    if _save_config(config_dict):
        summary["steps"].append("saved")

    print("Done. Try: hermes x402 status   |   hermes (chat and let it pay for tools)")
    return summary
