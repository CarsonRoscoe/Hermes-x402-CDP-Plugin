"""`hermes x402 wallet | fund | balance` subcommands.

Reads route through the local CDP wallet facade.
"""

from __future__ import annotations

from .. import config, wallet

_UNAVAILABLE = "wallet address unavailable (check `hermes x402 status` and CDP credentials)"


def wallet_command(args) -> int:
    """Show the wallet address for the active provider."""
    addr = wallet.address()
    if not addr:
        print(_UNAVAILABLE)
        return 1
    print(f"x402 wallet: {addr}")
    return 0


def fund_command(args) -> int:
    """Print funding instructions appropriate to the network."""
    addr = wallet.address()
    net = config.network()
    if not addr:
        print(_UNAVAILABLE)
        return 1
    print("Fund your x402 wallet")
    print(f"  Address ({net}):")
    print(f"    {addr}")
    if config.is_testnet(net):
        print("  Testnet: ask the agent to call cdp_faucet (or `hermes x402 balance` to verify).")
    else:
        print("  Mainnet: ask the agent to call cdp_onramp to buy USDC with fiat, or send USDC here.")
    print("  Then check it arrived with: hermes x402 balance")
    return 0


def balance_command(args) -> int:
    """Show USDC balance for the active provider."""
    net = config.network()
    bal = wallet.usdc_balance(net)
    if bal is None:
        print(f"balance unavailable on {net} (check `hermes x402 status` and CDP credentials)")
        return 1
    print(f"{bal} USDC on {net} ({wallet.address() or 'unknown address'})")
    return 0
