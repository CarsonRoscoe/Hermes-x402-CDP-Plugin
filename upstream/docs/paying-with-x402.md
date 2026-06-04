# Paying with x402

Hermes can pay for HTTP and MCP **tools** with USDC micropayments over the
[x402](https://github.com/x402-foundation/x402) protocol. Payment signing is delegated to a
Coinbase MCP server (OAuth'd custodial wallet) — no per-service API keys and no key material
in the agent. (Paying for *inference* via `provider: x402` is out of scope for now.)

## Quick start

```bash
pip install hermes-x402
hermes setup --coinbase     # connect the signer + register the Coinbase/Bazaar MCP servers
hermes x402 status
```

Or step by step:

```bash
hermes plugins enable hermes-x402
hermes x402 init
hermes x402 fund            # prints the address to send USDC (Base) to
hermes x402 balance
```

## What you get

- Native discovery: the CDP Bazaar MCP is registered in `mcp_servers`, so the agent calls
  `mcp_bazaar_search_resources` / `mcp_bazaar_proxy_tool_call` directly.
- Two payment tools: `x402_request` (paid HTTP to a known URL) and
  `x402_retry_mcp_payment` (pay + retry any `mcp_*` call that returned payment-required).
- Per-call and per-session USDC budgets (`x402.max_price_usdc`, `x402.session_budget_usdc`).
- A spend ledger: `hermes x402 spend` / `hermes x402 payments`.

## How it works

The `hermes-x402` companion package owns the Coinbase MCP connection and the x402 payment
seam (`create_payment_payload`), and registers the Coinbase + Bazaar MCP servers during
onboarding. The **wallet and signing live in the Coinbase MCP**, not the agent. Hermes core
only ships the `--coinbase` setup flag.
