# Paying with x402

Hermes can pay for HTTP and MCP **tools** with USDC micropayments over the
[x402](https://github.com/x402-foundation/x402) protocol. By default the plugin uses a
**self-custodial CDP server wallet** (keys stored locally in your CDP account, signed
in-process via the CDP SDK). A hosted Coinbase MCP signer is available as an alternative
provider (`x402.provider: coinbase_mcp`) but is not yet released.
(Paying for *inference* via `provider: x402` is out of scope for now.)

## Quick start

```bash
pip install hermes-x402
# Add CDP credentials to ~/.hermes/.env:
#   CDP_API_KEY_ID=...
#   CDP_API_KEY_SECRET=...
#   CDP_WALLET_SECRET=...
hermes setup --coinbase     # provision the CDP wallet + register the Bazaar MCP server
hermes x402 status
```

Or step by step:

```bash
hermes plugins enable hermes-x402
hermes x402 init
hermes x402 balance         # check wallet balance (use cdp_faucet on testnet to fund)
```

## What you get

- A self-custodial CDP server wallet with native `cdp_*` tools (status, balance, faucet,
  onramp, transfer) for wallet management.
- Native discovery: the CDP Bazaar MCP is registered in `mcp_servers`, so the agent calls
  `mcp_bazaar_search_resources` / `mcp_bazaar_proxy_tool_call` directly.
- Two payment tools: `x402_request` (paid HTTP to a known URL) and
  `x402_retry_mcp_payment` (pay + retry any `mcp_*` call that returned payment-required).
- Per-call and per-session USDC budgets (`x402.max_price_usdc`, `x402.session_budget_usdc`).
- A spend ledger: `hermes x402 spend` / `hermes x402 payments`.

## How it works

The `hermes-x402` companion package provisions a CDP server wallet and owns the x402
payment seam (`create_payment_payload`). Signing happens in-process via the CDP SDK —
the wallet private key never leaves the CDP platform. Hermes core only ships the
`--coinbase` setup flag; all payment logic lives in the companion plugin.
