# Examples

Scripts showing how the `hermes-x402` plugin pays for x402 services. Payment **signing is
delegated to a Coinbase MCP server** — in dev that's the local `fake-coinbase-mcp`, so no
keys or cloud credentials are needed.

| Example | Shows |
|---------|-------|
| `pay_for_http_service.py` | Pay an x402 HTTP endpoint via the plugin's `x402_request` (signed by the Coinbase MCP) |
| `pay_for_mcp_tool.py` | Pay + retry a native `mcp_*` call via `x402_retry_mcp_payment` |
| `monetize_endpoint.py` | Charge for your own MCP tool with `monetize.paid_tool` |
| `onboarding_walkthrough.md` | What `hermes setup --coinbase` does end to end |
| `self_publish_walkthrough.md` | An agent monetizing its own endpoint |

Discovery is **native**: onboarding registers the CDP Bazaar MCP and the Coinbase MCP under
Hermes `mcp_servers`, so the agent calls `mcp_bazaar_search_resources` /
`mcp_bazaar_proxy_tool_call` directly and pays reactively with `x402_retry_mcp_payment`.

## Dev setup (against the fake Coinbase MCP)

```bash
# 1. Install the plugin and the local fake signer
pip install -e hermes-x402
pip install -e fake-coinbase-mcp        # provides the `fake-coinbase-mcp` stdio server

# 2. Point the plugin at the fake (default config already does this):
#    ~/.hermes/config.yaml
#    x402:
#      coinbase_mcp:
#        transport: stdio
#        command: fake-coinbase-mcp

python examples/pay_for_http_service.py https://some-x402-endpoint.example/data
```

The fake signs with a throwaway local key (or a CDP server wallet if `CDP_*` is set). When
the real Coinbase MCP ships, switch `transport: remote` + OAuth — the example code is
unchanged.
