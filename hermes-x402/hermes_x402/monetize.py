"""Monetize / self-publish helper (stretch goal).

Lets an agent stand up a *paid* MCP tool: wrap a FastMCP tool with x402's
``create_payment_wrapper`` backed by an ``x402ResourceServer`` on the CDP facilitator, and
declare bazaar discovery metadata so the endpoint becomes discoverable. After one
successful settlement through the CDP facilitator, the resource is indexed in the Coinbase
Bazaar automatically.

This is the "agents self-publish x402 endpoints" direction. Field names on
``ResourceConfig`` / discovery helpers can vary across x402 SDK versions; this helper
keeps the wiring in one place and degrades with a clear error if the SDK shape differs.

Requires ``[monetize]`` (fastmcp) + a configured CDP facilitator.
"""

from __future__ import annotations

import logging

from . import config

logger = logging.getLogger(__name__)


def build_resource_server(network: str | None = None):
    """Create + initialize an ``x402ResourceServer`` on the CDP facilitator."""
    from x402 import x402ResourceServer
    from x402.http import HTTPFacilitatorClient
    from x402.mechanisms.evm.exact.register import register_exact_evm_server

    from .facilitator import facilitator_config

    network = network or config.network()
    server = x402ResourceServer(HTTPFacilitatorClient(facilitator_config()))
    register_exact_evm_server(server, networks=config.caip2(network))
    server.initialize()
    return server


def paid_tool(
    *,
    price_usdc: str | float,
    resource_url: str,
    pay_to: str | None = None,
    description: str = "",
    network: str | None = None,
):
    """Return a decorator that charges ``price_usdc`` to call a FastMCP tool.

    ``pay_to`` defaults to the local wallet address, so an agent can monetize a tool with
    payouts to its own wallet without hardcoding an address.

    Usage::

        from hermes_x402.monetize import paid_tool

        charge = paid_tool(price_usdc="0.01", resource_url="mcp://tool/get_weather")

        @mcp.tool(name="get_weather")
        @charge
        async def get_weather(city: str) -> str:
            return json.dumps({"city": city, "forecast": "sunny"})
    """
    from x402.extensions.bazaar import declare_mcp_discovery_extension
    from x402.mcp import ResourceInfo, create_payment_wrapper
    from x402.schemas import ResourceConfig

    if pay_to is None:
        from . import wallet

        pay_to = wallet.address()
        if not pay_to:
            raise ValueError(
                "pay_to not given and the wallet provider did not report an address"
            )

    server = build_resource_server(network)
    accepts = server.build_payment_requirements(
        ResourceConfig(pay_to=pay_to, price=str(price_usdc))
    )
    extensions = declare_mcp_discovery_extension()
    return create_payment_wrapper(
        server,
        accepts=accepts,
        resource=ResourceInfo(url=resource_url),
        extensions=extensions,
    )
