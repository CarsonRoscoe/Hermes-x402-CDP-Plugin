"""`hermes x402 wallet | fund | balance` subcommands.

Reads come from the Coinbase MCP (via the ``coinbase_mcp.wallet`` facade); the wallet is
custodied by the Coinbase MCP, not the plugin.
"""

from __future__ import annotations

from .. import config


def wallet_command(args) -> int:
    """Show the wallet address (as reported by the Coinbase MCP)."""
    from ..coinbase_mcp import wallet

    addr = wallet.address()
    if not addr:
        print("wallet address unavailable (is the Coinbase MCP configured/reachable?)")
        return 1
    print(f"x402 wallet: {addr}")
    return 0


def fund_command(args) -> int:
    """Print funding instructions (send USDC on Base to the wallet address)."""
    from ..coinbase_mcp import wallet

    addr = wallet.address()
    net = config.network()
    if not addr:
        print("wallet address unavailable (is the Coinbase MCP configured/reachable?)")
        return 1
    print("Fund your x402 wallet")
    print(f"  Send USDC on {net} to:")
    print(f"    {addr}")
    print("  Then check it arrived with: hermes x402 balance")
    return 0


def balance_command(args) -> int:
    """Show USDC balance (as reported by the Coinbase MCP)."""
    from ..coinbase_mcp import wallet

    net = config.network()
    bal = wallet.usdc_balance(net)
    if bal is None:
        print(f"balance unavailable on {net} (is the Coinbase MCP configured/reachable?)")
        return 1
    print(f"{bal} USDC on {net} ({wallet.address() or 'unknown address'})")
    return 0
