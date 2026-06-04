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
| Coinbase MCP | OAuth / CAT (dev: none) | `create_payment_payload`, `coinbase_balance`, `coinbase_status`, etc. | Plugin also connects internally for `x402_retry_mcp_payment` signing |
| Bazaar MCP | None | `search_resources`, `proxy_tool_call` | Public endpoint, no auth needed |
| Any other paid MCP server | Per-server | All their tools natively | Paid tools handled by `x402_retry_mcp_payment` |

## Plugin tool surface (two tools)

| Tool | Purpose |
|---|---|
| `x402_request` | Paid HTTP request to a known URL |
| `x402_retry_mcp_payment` | Retry any MCP tool that returned payment-required |

## How `x402_retry_mcp_payment` works

1. Agent calls any `mcp_*` tool → gets a payment-required result (often just an error string)
2. Skill: call `x402_retry_mcp_payment(tool_name, arguments)` (pass `payment_required` only if you have the structured details — best-effort)
3. Plugin parses `mcp_{server}_{tool}` → looks up server URL from Hermes `mcp_servers` config, recovering the real upstream tool name from the server's tool list
4. Calls Coinbase MCP `create_payment_payload(...)` → signed `PaymentPayload` (re-probing the server for the requirement when `payment_required` was not supplied)
5. Connects to the server, retries with `_meta["x402/payment"]`
6. Returns the real result

The agent uses native tool names throughout. It can also call `create_payment_payload` directly if it wants to understand the payment — the plugin is transparent, not a black box.
