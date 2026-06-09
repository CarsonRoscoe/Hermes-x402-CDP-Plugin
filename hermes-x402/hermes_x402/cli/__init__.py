"""The ``hermes x402`` CLI subcommand tree.

``register_cli(subparser)`` builds the argparse tree; ``x402_command(args)`` dispatches
to the per-subcommand handlers. Wired into Hermes via ``ctx.register_cli_command`` in the
package root, so ``hermes x402 <subcommand>`` works with no change to Hermes core.
"""

from __future__ import annotations

import argparse

from .onboarding import init_command
from .reconcile import reconcile_command
from .spend import payments_command, spend_command
from .status import status_command
from .wallet import balance_command, fund_command, wallet_command


def register_cli(subparser: argparse.ArgumentParser) -> None:
    """Build the ``hermes x402`` argparse tree."""
    subs = subparser.add_subparsers(dest="x402_command")

    subs.add_parser("init", help="Onboarding: choose wallet provider, provision wallet, register MCP servers")
    subs.add_parser("wallet", help="Show the x402 wallet address")
    subs.add_parser("fund", help="Print address + instructions to add USDC on Base")
    subs.add_parser("balance", help="Show on-chain USDC balance")
    subs.add_parser("status", help="Show wallet (signer) + balance")
    subs.add_parser("spend", help="Show recent x402 spend total")
    subs.add_parser("payments", help="List recent x402 payments")
    rec = subs.add_parser("reconcile", help="List/resolve unconfirmed paid operations")
    rec.add_argument("--resolve", help="Journal entry id to mark resolved")
    rec.add_argument("--state", help="Resolved state: paid | failed | succeeded")

    subparser.set_defaults(func=x402_command)


_DISPATCH = {
    "init": init_command,
    "wallet": wallet_command,
    "fund": fund_command,
    "balance": balance_command,
    "status": status_command,
    "spend": spend_command,
    "payments": payments_command,
    "reconcile": reconcile_command,
}


def x402_command(args) -> int:
    """Dispatch ``hermes x402 <subcommand>``."""
    sub = getattr(args, "x402_command", None)
    if not sub:
        print("usage: hermes x402 {init,wallet,fund,balance,status,spend,payments,reconcile}")
        return 2
    handler = _DISPATCH.get(sub)
    if handler is None:
        print(f"unknown x402 subcommand: {sub}")
        return 2
    return handler(args)
