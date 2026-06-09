---
name: x402-payments
description: >-
  Pay for APIs and MCP tools with USDC via x402 — direct HTTP endpoints and CDP Bazaar services
  (mcp_bazaar_proxy_tool_call). Use when calling a paid API, discovering/paying for a Bazaar or
  MCP tool, funding the wallet, or whenever a tool returns payment-required (402).
version: 0.0.1
author: Coinbase
license: Apache-2.0
platforms: [linux, macos]
triggers:
  - user asks to call, discover, or pay for a CDP Bazaar service
  - user mentions mcp_bazaar_search_resources or mcp_bazaar_proxy_tool_call
  - mcp_bazaar_proxy_tool_call returns a 402 / payment-required result
  - any mcp_* tool returns payment-required and needs x402_retry_mcp_payment
  - user asks to call a paid HTTP API or use x402_request
  - x402_request fails with payment_rejected_402 or payment_signing_failed
  - user asks to fund the x402 wallet, faucet USDC, or check wallet balance/address
metadata:
  hermes:
    tags: [x402, payments, usdc, cdp, paid-api, bazaar, mcp]
    category: blockchain
---

# x402 Payments Skill (Remote Coinbase MCP)

How to discover, fund, and pay for x402-enabled services. This deployment signs and manages
the wallet via a **Coinbase MCP server** (tools prefixed `mcp_coinbase_*`). This skill covers
the full flow from funding through calling a paid endpoint. Run `hermes x402 init` once before
using paid tools.

> Provider note: the same capabilities exist in the "Local CDP Tools" provider under `cdp_*`
> tool names (e.g. `cdp_wallet_balance`, `cdp_faucet`, `cdp_onramp`). In this deployment the
> wallet is the remote Coinbase MCP, so use the `mcp_coinbase_*` tools below.

## Available tools (use these — don't reinvent them)

### Plugin tools (always present)
- `x402_request` — make a paid HTTP call to a known URL. Handles 402 → sign → retry.
- `x402_retry_mcp_payment` — pay and retry any `mcp_*` call that returned payment-required.

### Native Coinbase MCP tools (`mcp_coinbase_*`)
These are registered automatically during onboarding. Use them directly by name.

| Tool | Purpose |
|---|---|
| `mcp_coinbase_faucet_usdc` | Get 1 testnet USDC on base-sepolia (10 claims/day) |
| `mcp_coinbase_faucet_eth` | Get 0.0001 testnet ETH on base-sepolia (1000 claims/day) |
| `mcp_coinbase_coinbase_balance` | Check real on-chain wallet balance |
| `mcp_coinbase_coinbase_status` | Get wallet address |

**Always use `mcp_coinbase_faucet_usdc` when the wallet needs testnet USDC.** Never use
Circle/Alchemy/browser faucets — those require human reCAPTCHA. The CDP faucet works
programmatically via the registered MCP server.

### Native Bazaar MCP tools (`mcp_bazaar_*`)
- `mcp_bazaar_search_resources` — discover paid services by keyword.
- `mcp_bazaar_proxy_tool_call` — invoke a discovered Bazaar service.

## Typical flows

### Pay a known HTTP endpoint
```
x402_request(url="https://...", max_price_usdc=0.10)
```
If rejected with `payment_rejected_402` and `insufficient_funds`:
1. Call `mcp_coinbase_faucet_usdc` (testnet) or fund mainnet wallet manually.
2. Retry `x402_request`.

### Discover and pay a Bazaar service (the ONLY correct flow)
1. `mcp_bazaar_search_resources(query="jokes")` → pick a service; note its `toolName`
   (it looks like `x402_get_…`).
2. `mcp_bazaar_proxy_tool_call(toolName="x402_get_…", parameters={…})` → returns
   payment-required (402).
3. Pay by retrying the **proxy** tool:
```
x402_retry_mcp_payment(
    tool_name="mcp_bazaar_proxy_tool_call",          # the proxy tool — NOT the x402_get_… name
    arguments={"toolName": "x402_get_…", "parameters": {…}}   # identical to step 2
)
```
`x402_retry_mcp_payment` re-issues the proxy call with a signed payment, which the Bazaar
forwards to the upstream service, and returns the paid result.

**Do NOT:** pass the `x402_get_…` name as `tool_name`, call that name as a tool directly, or fall
back to `x402_request` on the resource URL. The `x402_get_…` name is a *discovered resource*,
reachable only through the proxy — it is not a registered MCP tool.

### Check balance before making multiple paid calls
```
mcp_coinbase_coinbase_balance(network="base-sepolia")
```

## Error codes from x402_request

| error | Meaning | Action |
|---|---|---|
| `payment_rejected_402` + `insufficient_funds` | Wallet has no USDC | Call `mcp_coinbase_faucet_usdc` then retry |
| `payment_rejected_402` + `invalid signature` | Signing bug | Report bug; check `hermes x402 status` |
| `payment_not_attempted_402` | Not an x402 endpoint or header missing | Verify URL |
| `payment_signing_failed` | Signer connection error | Run `hermes x402 status` |
| `payment_exceeds_cap` | Price > max_price_usdc | Raise cap or choose different service |

## Identifying EIP-3009 compatible services

The Coinbase MCP signer only supports EIP-3009 (not Permit2). A Bazaar service is
EIP-3009 compatible when its `accepts[]` entry matches either of:

- `extra.assetTransferMethod == "erc3009"` (explicit)
- `assetTransferMethod` absent — defaults to EIP-3009 (e.g. `extra: {name: "USD Coin", version: "2"}`)

A service using `assetTransferMethod == "permit2"` will be rejected at signing time with a
clear error. Prefer services that omit `assetTransferMethod` or set it to `"erc3009"`.

## Network context
- **base-sepolia** (testnet): use CDP faucet, no real USDC needed.
- **base** (mainnet): requires real USDC; fund wallet via Coinbase.
- Current network: check `hermes x402 status` or `mcp_coinbase_coinbase_balance`.

## Common mistakes (and the error they produce)
| You did | You'll see | Fix |
|---|---|---|
| Passed the discovered `x402_…` name as `tool_name` | `wrong_tool_name_for_retry` (or `no mcp_servers entry matches tool 'x402_…'`) | Re-call with `tool_name="mcp_bazaar_proxy_tool_call"` and `arguments={toolName, parameters}` — the response's `fix` field is ready to use verbatim |
| Fell back to `x402_request` on the resource URL for a Bazaar service | bypasses the proxy | Use the proxy flow above; `x402_request` is for direct URLs only |
| Hand-built `payment_required` | unnecessary | Leave it empty; the tool re-probes the server |

## Pitfalls
- **Retry the tool you called, not the resource it proxies.** For a Bazaar service that is
  `mcp_bazaar_proxy_tool_call`, never the discovered `x402_…` name.
- Never call `x402_retry_mcp_payment` preemptively — only after a real payment-required result.
- `mcp_coinbase_faucet_usdc` is testnet only (base-sepolia). Do not call it for mainnet.
- `x402_retry_mcp_payment` supports URL-based MCP servers only (not stdio).
- **Bazaar proxy + `x402_retry_mcp_payment`:** The signed payment travels in
  `_meta["x402/payment"]`; the Bazaar proxy re-marshals it into a `PAYMENT-SIGNATURE` header for
  the upstream. If you hit `payment_attempted_but_rejected`, read `server_error`/`raw_content`
  for the upstream's reason (common causes: insufficient balance, expired/reused authorization,
  asset/network mismatch). `x402_request` against the direct upstream URL is a useful diagnostic.
- After funding, allow ~15 seconds for the faucet transaction to confirm before retrying.

## Verification
A successful paid result includes `payment: {...}` (HTTP) or `payment_settled: true` (MCP).
Note: `payment_made: true` means signing was attempted; `payment_settled: true` means the
facilitator confirmed the transaction — only the latter guarantees funds moved and the service
was rendered. Use `hermes x402 payments` or `hermes x402 balance` to review spend and wallet state.

## Recovery from unknown_settlement errors
If a paid call returns `error: "unknown_settlement"`, money may or may not have moved. Do
not assume failure. Check `hermes x402 payments` to see if the transaction appears. If you
need to retry, pass `override=true`:
```
x402_request(url="https://...", override=true)
```
