---
name: x402-payments
description: >-
  Pay for APIs and MCP tools with USDC via x402, and manage a self-custodial CDP server
  wallet with native cdp_* tools (status, balance, testnet faucet, fiat onramp, transfer).
  Use when calling a paid API, discovering/paying for a Bazaar or MCP tool, funding the
  wallet, or whenever a tool returns payment-required (402).
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
  - user asks to fund the wallet, faucet USDC, buy USDC, or check wallet balance/address
metadata:
  hermes:
    tags: [x402, payments, usdc, cdp, paid-api, bazaar, mcp, wallet]
    category: blockchain
---

# x402 Payments Skill (Local CDP wallet)

How to discover, fund, and pay for x402-enabled services. This deployment uses a
**self-custodial CDP server wallet** managed in-process via native `cdp_*` tools (CDP
credentials in `~/.hermes/.env`). Run `hermes x402 init` once before using paid tools.

> Provider note: the same capabilities exist in the "Remote Coinbase MCP" provider under
> `mcp_coinbase_*` tool names (e.g. `mcp_coinbase_coinbase_balance`). In this deployment the
> wallet is local, so use the `cdp_*` tools below.

## Available tools (use these — don't reinvent them)

### Paid-call tools (always present)
- `x402_request` — make a paid HTTP call to a known URL. Handles 402 -> sign -> retry.
- `x402_retry_mcp_payment` — pay and retry any `mcp_*` call that returned payment-required.

### Local CDP wallet tools (`cdp_*`)
These implement the x402 Hermes Wallet Interface (see
`docs/wallet-tool-interfaces.md`) for the local provider.

| Tool | Inputs | Outputs | Purpose |
|---|---|---|---|
| `cdp_wallet_status` | — | address, account_name, network, provider | Find the wallet address / confirm provisioning |
| `cdp_wallet_balance` | `network?`, `asset?` | `balances[]` {symbol, amount, decimals, contract}, plus `eth`/`usdc` | Check funds before paying or transferring |
| `cdp_faucet` | `token` (usdc/eth), `network?` | tx_hash, explorer | Get free TESTNET funds (base-sepolia etc.) |
| `cdp_onramp` | `asset?`, `network?`, `amount?`, `currency?`, `country?`, `subdivision?` | onramp_url | Buy USDC/ETH with fiat (MAINNET funding) |
| `cdp_transfer` | `to`, `amount`, `token?`, `network?`, `override?` | tx_hash, explorer | Send USDC/ETH to an address (moves real funds) |
| `cdp_payments` | `limit?`, `since?` | `payments[]` {endpoint, amount_usdc, tx, settled, timestamp}, total_usdc | Reconcile spend / confirm a paid call settled |

x402 signing itself is automatic: `x402_request` / `x402_retry_mcp_payment` sign with this
wallet under the hood — you never call a signing tool directly.

**Funding rules:**
- **Testnet** (base-sepolia): use `cdp_faucet` (token `usdc` or `eth`). Never use
  Circle/Alchemy/browser faucets — those require human reCAPTCHA. The CDP faucet works
  programmatically.
- **Mainnet** (base): use `cdp_onramp` to get a Coinbase Onramp URL the user opens to buy
  with fiat, or have funds sent to the address from `cdp_wallet_status`.

### Native Bazaar MCP tools (`mcp_bazaar_*`)
- `mcp_bazaar_search_resources` — discover paid services by keyword.
- `mcp_bazaar_proxy_tool_call` — invoke a discovered Bazaar service.

## Typical flows

### Pay a known HTTP endpoint
```
x402_request(url="https://...", max_price_usdc=0.10)
```
If rejected with `payment_rejected_402` and `insufficient_funds`:
1. `cdp_faucet` (testnet) or `cdp_onramp` (mainnet) to fund the wallet.
2. Retry `x402_request`.

### Discover and pay a Bazaar service (the ONLY correct flow)
1. `mcp_bazaar_search_resources(query="jokes")` -> pick a service; note its `toolName`
   (it looks like `x402_get_...`).
2. `mcp_bazaar_proxy_tool_call(toolName="x402_get_...", parameters={...})` -> returns
   payment-required (402).
3. Pay by retrying the **proxy** tool:
```
x402_retry_mcp_payment(
    tool_name="mcp_bazaar_proxy_tool_call",          # the proxy tool — NOT the x402_get_... name
    arguments={"toolName": "x402_get_...", "parameters": {...}}   # identical to step 2
)
```

**Do NOT:** pass the `x402_get_...` name as `tool_name`, call that name as a tool directly,
or fall back to `x402_request` on the resource URL. The `x402_get_...` name is a *discovered
resource*, reachable only through the proxy — it is not a registered tool.

### Check balance before making multiple paid calls
```
cdp_wallet_balance(network="base-sepolia")
```

### Send funds out (sensitive)
```
cdp_transfer(to="0x...", amount=1.5, token="usdc")
```
Only when explicitly instructed. USDC transfers above `x402.max_price_usdc` are refused
unless `override=true`.

### Reconcile spend (receipts)
```
cdp_payments(limit=20)
```
Returns recent payments (endpoint, amount_usdc, tx, settled, timestamp). Use to confirm a
paid call settled or to report total spend.

## Error codes from x402_request
| error | Meaning | Action |
|---|---|---|
| `payment_rejected_402` + `insufficient_funds` | Wallet has no USDC | `cdp_faucet` (testnet) / `cdp_onramp` (mainnet), then retry |
| `payment_rejected_402` + `invalid signature` | Signing bug | Report bug; check `hermes x402 status` |
| `payment_not_attempted_402` | Not an x402 endpoint or header missing | Verify URL |
| `payment_signing_failed` | CDP wallet/credential error | Run `hermes x402 status`; check `~/.hermes/.env` |
| `payment_exceeds_cap` | Price > max_price_usdc | Raise cap or choose a different service |

## Identifying EIP-3009 compatible services
The CDP wallet signs **EIP-3009** (not Permit2). A Bazaar service is EIP-3009 compatible when
its `accepts[]` entry matches either:
- `extra.assetTransferMethod == "erc3009"` (explicit), or
- `assetTransferMethod` absent — defaults to EIP-3009 (e.g. `extra: {name: "USD Coin", version: "2"}`).
A service using `assetTransferMethod == "permit2"` is skipped at signing time.

## Network context
- **base-sepolia** (testnet): use `cdp_faucet`, no real USDC needed.
- **base** (mainnet): requires real USDC; fund via `cdp_onramp` or a direct transfer.
- Current network/provider: check `hermes x402 status` or `cdp_wallet_status`.

## Pitfalls
- `cdp_faucet` is testnet only; it errors on mainnet. Use `cdp_onramp` for mainnet funding.
- After funding, allow ~15 seconds for the transaction to confirm before retrying.
- Retry the tool you called, not the resource it proxies (Bazaar = `mcp_bazaar_proxy_tool_call`).
- Never call `x402_retry_mcp_payment` preemptively — only after a real payment-required result.
- `cdp_transfer` moves real funds; confirm the recipient and amount.

## Verification
A successful paid result includes `payment: {...}` (HTTP) or `payment_made: true` (MCP).
Use `cdp_payments` (or `cdp_wallet_balance`) to review spend and wallet state.
