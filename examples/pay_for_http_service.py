"""Pay for an x402-enabled HTTP service via the plugin.

This calls the plugin's ``x402_request`` tool exactly as the Hermes agent would. The 402 ->
sign -> retry flow is handled by the x402 SDK; the signature comes from the Coinbase MCP
(the local ``fake-coinbase-mcp`` in dev). No keys live in this process.

Run (see examples/README.md for setup):
    pip install -e hermes-x402 -e fake-coinbase-mcp
    python examples/pay_for_http_service.py https://some-x402-endpoint.example/data
"""

from __future__ import annotations

import sys


def main(url: str) -> None:
    from hermes_x402.tools.request import x402_request

    # max_price_usdc caps the spend; the Coinbase MCP picks which requirement to pay.
    print(x402_request({"url": url, "method": "GET", "max_price_usdc": 0.10}))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "https://x402-endpoint.example/resource")
