"""Pay + retry a native MCP tool call that returned payment-required.

In Hermes, the agent first calls a native ``mcp_*`` tool (e.g. the Bazaar's
``mcp_<bazaar_server>_proxy_tool_call`` or any paid MCP server registered in
``mcp_servers``). If
that call comes back payment-required, the agent calls ``x402_retry_mcp_payment`` with the
SAME tool name and arguments — the plugin resolves the server URL from ``mcp_servers``,
signs via the self-custodial CDP server wallet, and re-issues the call with the payment
attached.

This script simulates that second step directly. ``tool_name`` must match an entry written
into your Hermes ``mcp_servers`` config (run ``hermes x402 init`` first).

Run:
    python examples/pay_for_mcp_tool.py mcp_bazaar_proxy_tool_call
"""

from __future__ import annotations

import json
import sys


def _default_tool_name() -> str:
    try:
        from hermes_x402.tools.retry_mcp import _find_bazaar_proxy_tool, _load_mcp_servers

        return _find_bazaar_proxy_tool(_load_mcp_servers()) or "mcp_bazaar_proxy_tool_call"
    except Exception:
        return "mcp_bazaar_proxy_tool_call"


def main(tool_name: str) -> None:
    from hermes_x402.tools.retry_mcp import x402_retry_mcp_payment

    # The arguments are whatever the original native mcp_* call used. For the Bazaar proxy
    # that's the discovered service name + its parameters.
    arguments = {"toolName": "example_service", "parameters": {}}
    print(x402_retry_mcp_payment({"tool_name": tool_name, "arguments": arguments, "max_price_usdc": 0.10}))


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else _default_tool_name()
    try:
        main(name)
    except Exception as exc:  # surface config/connection issues clearly in the example
        print(json.dumps({"error": str(exc)}))
