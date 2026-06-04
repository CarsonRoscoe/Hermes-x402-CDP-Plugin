"""Monetize your own MCP tool with x402 + the CDP facilitator (self-publish).

Uses the plugin's ``hermes_x402.monetize.paid_tool`` helper to charge other agents USDC to
call a FastMCP tool, settled through the CDP facilitator and declared for bazaar discovery.
The payout address defaults to the wallet the Coinbase MCP reports (override with
``PAYOUT_ADDRESS``).

Run:
    pip install "hermes-x402[monetize]"
    export CDP_API_KEY_ID=... CDP_API_KEY_SECRET=...   # facilitator (verify/settle)
    python examples/monetize_endpoint.py
"""

from __future__ import annotations

import json
import os


def build_server():
    from fastmcp import FastMCP

    from hermes_x402.monetize import paid_tool

    charge = paid_tool(
        price_usdc="0.01",
        resource_url="mcp://tool/get_weather",
        pay_to=os.environ.get("PAYOUT_ADDRESS"),  # None => use Coinbase MCP wallet
        description="Current weather by city",
    )

    mcp = FastMCP("paid-weather")

    @mcp.tool(name="get_weather", description="Get current weather for a city")
    @charge
    async def get_weather(city: str) -> str:
        return json.dumps({"city": city, "forecast": "sunny", "temp_c": 21})

    return mcp


if __name__ == "__main__":
    build_server().run()
