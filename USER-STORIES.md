# User Story Combinations

## The matrix

| # | How agent finds resource | Agent does |
|---|---|---|
| 1 | Given an HTTP URL directly | `x402_request(url)` |
| 2 | Discovers via Bazaar | `mcp_bazaar_search_resources(query)` → `mcp_bazaar_proxy_tool_call(tool_name, params)` → payment-required → `x402_retry_mcp_payment(...)` |
| 3 | Connected MCP server with a paid tool | native `mcp_*` tool call → payment-required → `x402_retry_mcp_payment(...)` |

Rows 2 and 3 share the same payment path.

## MCP servers (all registered in Hermes `mcp_servers:`)

| Server | Auth | Agent sees | Notes |
|---|---|---|---|
| Bazaar MCP | None | `search_resources`, `proxy_tool_call` | Public endpoint, no auth needed |
| Any other paid MCP server | Per-server | All their tools natively | Paid tools handled by `x402_retry_mcp_payment` |

The local CDP wallet is the only selectable signer provider today. Remote Coinbase MCP
signing remains future work; onboarding registers Bazaar and removes stale `coinbase`
signer entries.

## Plugin tool surface (two tools)

| Tool | Purpose |
|---|---|
| `x402_request` | Paid HTTP request to a known URL |
| `x402_retry_mcp_payment` | Retry any MCP tool that returned payment-required |

## How `x402_retry_mcp_payment` works

1. Agent calls any `mcp_*` tool → gets a payment-required result (often just an error string)
2. Skill: call `x402_retry_mcp_payment(tool_name, arguments)` (pass `payment_required` only if you have the structured details — best-effort)
3. Plugin parses `mcp_{server}_{tool}` → looks up server URL from Hermes `mcp_servers` config, recovering the real upstream tool name from the server's tool list
4. Calls the local CDP signer through the shared `create_payment_payload(...)` seam → signed `PaymentPayload` (re-probing the server for the requirement when `payment_required` was not supplied)
5. Connects to the server, retries with `_meta["x402/payment"]`
6. Returns the real result

The agent uses native tool names throughout. Payment signing stays internal to
`x402_request` / `x402_retry_mcp_payment` so the model follows one safe path.
