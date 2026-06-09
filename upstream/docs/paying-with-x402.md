# Paying with x402

> **Note:** The `hermes setup --coinbase` flag described below requires a separate PR to
> `hermes-agent` that is not yet merged. Until that PR lands, use `hermes x402 init`
> from the companion CLI directly. All other functionality described here is available
> via the `hermes-x402` pip package today.

Hermes can pay for HTTP and MCP **tools** with USDC micropayments over the
[x402](https://github.com/x402-foundation/x402) protocol. By default the plugin uses a
**self-custodial CDP server wallet** (keys stored locally in your CDP account, signed
in-process via the CDP SDK). This local wallet provider is the only selectable provider
today; a hosted Coinbase MCP signer is future work and is not enabled by config.
(Paying for *inference* via `provider: x402` is out of scope for now.)

## Quick start

```bash
pip install hermes-x402
# Add CDP credentials to ~/.hermes/.env:
#   CDP_API_KEY_ID=...
#   CDP_API_KEY_SECRET=...
#   CDP_WALLET_SECRET=...
# Companion CLI (always available):
hermes x402 init      # provisions wallet, registers Bazaar MCP, enables plugin in config
hermes x402 status
# If your Hermes build includes the upstream --coinbase flag (requires hermes-agent PR):
# hermes setup --coinbase
```

Or step by step:

```bash
# pip plugins are opt-in via config:
# plugins:
#   enabled: [hermes-x402]
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
  The session budget covers x402 paid HTTP/MCP calls, not direct wallet transfers through
  `cdp_transfer`.
- A spend ledger: `hermes x402 spend` / `hermes x402 payments`.

## How it works

The `hermes-x402` companion package provisions a CDP server wallet and owns the x402
payment seam (`create_payment_payload`). Signing happens in-process via the CDP SDK —
the wallet private key never leaves the CDP platform. Hermes core only ships the
`--coinbase` setup flag; all payment logic lives in the companion plugin.
