# Examples

Scripts showing how the `hermes-x402` plugin pays for x402 services. Payment signing uses
a **self-custodial CDP server wallet** (CDP API keys in `~/.hermes/.env`).

| Example | Shows |
|---------|-------|
| `pay_for_http_service.py` | Pay an x402 HTTP endpoint via the plugin's `x402_request` |
| `pay_for_mcp_tool.py` | Pay + retry a native `mcp_*` call via `x402_retry_mcp_payment` |
| `monetize_endpoint.py` | Charge for your own MCP tool with `monetize.paid_tool` |
| `onboarding_walkthrough.md` | What the `hermes setup --coinbase` upstream flow targets (same logic as `hermes x402 init`) |
| `self_publish_walkthrough.md` | An agent monetizing its own endpoint |

Discovery is **native**: onboarding registers the CDP Bazaar MCP under Hermes `mcp_servers`
so the agent calls `mcp_bazaar_search_resources` / `mcp_bazaar_proxy_tool_call` directly
and pays reactively with `x402_retry_mcp_payment`.

## Dev setup

```bash
# 1. Install the plugin
pip install -e hermes-x402

# 2. Add CDP credentials to ~/.hermes/.env
#    CDP_API_KEY_ID=...
#    CDP_API_KEY_SECRET=...
#    CDP_WALLET_SECRET=...

# 3. Initialise the wallet
hermes x402 init

# 4. Run an example
python examples/pay_for_http_service.py https://some-x402-endpoint.example/data
```
