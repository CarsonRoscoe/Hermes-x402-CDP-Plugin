"""`hermes x402 init` — onboarding.

Thin CLI wrapper over ``..setup_flow.run_x402_onboarding`` (the same entry point the
upstream ``hermes setup --coinbase`` flag calls). Connects the signer and registers the
Coinbase + Bazaar MCP servers in ``mcp_servers``.
"""

from __future__ import annotations


def init_command(args) -> int:
    """Run the onboarding flow."""
    from ..setup_flow import run_x402_onboarding

    try:
        run_x402_onboarding(config_dict=None)
    except Exception as exc:
        print(f"x402 init failed: {exc}")
        return 1
    return 0
