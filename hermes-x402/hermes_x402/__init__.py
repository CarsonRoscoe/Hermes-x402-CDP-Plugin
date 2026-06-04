"""hermes-x402 — x402 micropayments for Hermes Agent.

This module is the plugin entry point. Hermes calls ``register(ctx)`` exactly once
at startup (whether the plugin is pip-installed via the ``hermes_agent.plugins``
entry point or dropped into ``~/.hermes/plugins/``). ``register`` wires up every
surface this plugin exposes:

- two agent tools: ``x402_request`` (paid HTTP to a known URL) and
  ``x402_retry_mcp_payment`` (pay + retry any native ``mcp_*`` call that returned
  payment-required),
- the ``hermes x402 ...`` CLI subcommand tree,
- the ``/x402`` in-session slash command,
- the bundled ``x402-payments`` skill,
- a ``pre_tool_call`` budget gate + an ``on_session_end`` spend summary hook.

Discovery and paid-MCP calls happen natively: onboarding registers the Coinbase MCP
(signing) and the CDP Bazaar MCP (``search_resources`` / ``proxy_tool_call``) under
Hermes's ``mcp_servers``, so the agent calls them as ``mcp_*`` tools and pays reactively
via ``x402_retry_mcp_payment``. Payments use the ``exact`` scheme; paying for *inference*
is out of scope.

Everything here is intentionally defensive: each registration is wrapped so a stub
or a missing optional dependency disables that one surface without breaking the host
agent. Nothing in this file modifies Hermes core.
"""

from __future__ import annotations

import json
import logging

__version__ = "0.0.1"

logger = logging.getLogger(__name__)

# Namespace all agent tools under one toolset so users can enable/disable the
# whole x402 surface with `hermes tools`.
TOOLSET = "x402"


def _register_tools(ctx) -> None:
    """Register every agent tool from the ``tools`` package's TOOLS table."""
    from .tools import TOOLS

    for spec in TOOLS:
        ctx.register_tool(
            name=spec.name,
            toolset=TOOLSET,
            schema=spec.schema,
            handler=spec.handler,
            check_fn=spec.check_fn,
            emoji=spec.emoji,
        )


def _register_cli(ctx) -> None:
    """Register the ``hermes x402`` CLI subcommand tree."""
    from .cli import register_cli, x402_command

    ctx.register_cli_command(
        name="x402",
        help="Manage x402 payments: onboarding, wallet, funding, status, spend",
        setup_fn=register_cli,
        handler_fn=x402_command,
        description="x402 micropayments for Hermes (wallet, paid tools, onboarding).",
    )


def _register_slash(ctx) -> None:
    """Register the in-session ``/x402`` slash command (status snapshot)."""

    def _handle_x402(raw_args: str):
        from .cli.status import status_summary

        try:
            return status_summary(raw_args)
        except Exception as exc:  # never break the chat loop
            logger.debug("x402 slash command failed: %s", exc)
            return json.dumps({"error": f"x402 status unavailable: {exc}"})

    ctx.register_command(
        "x402",
        handler=_handle_x402,
        description="Show x402 wallet, signer, and balance",
    )


def _register_skills(ctx) -> None:
    """Register bundled, read-only plugin skills under the ``hermes-x402:`` namespace."""
    from pathlib import Path

    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.is_dir():
        return
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, skill_md)


def _register_hooks(ctx) -> None:
    """Attach lifecycle hooks: budget gate before paid tools, spend summary at end."""
    from .budget import pre_tool_call
    from .ledger import on_session_end

    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("on_session_end", on_session_end)


# Each surface is independent: a failure in one must not take down the others or
# the host agent.
_SURFACES = (
    ("tools", _register_tools),
    ("cli", _register_cli),
    ("slash command", _register_slash),
    ("skills", _register_skills),
    ("hooks", _register_hooks),
)


def register(ctx) -> None:
    """Plugin entry point. Called once by Hermes at startup."""
    for label, fn in _SURFACES:
        try:
            fn(ctx)
        except Exception as exc:
            logger.warning("hermes-x402: failed to register %s: %s", label, exc)
