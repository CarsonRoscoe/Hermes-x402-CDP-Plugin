# hermes-x402

> The companion plugin. x402 micropayments for [Hermes Agent](https://github.com/NousResearch/hermes-agent), with a self-custodial CDP server wallet.

This is the distributable Python package (`hermes-x402`, module `hermes_x402`). It is
both a **pip-installable Hermes plugin** (via the `hermes_agent.plugins` entry point)
and a **drop-in plugin directory** (copy `hermes_x402/` into `~/.hermes/plugins/`).

## Wallet provider

The plugin uses a **local self-custodial CDP server wallet** (`x402.provider: local`):
managed in-process via the CDP SDK, with native `cdp_*` wallet-management tools and local
x402 signing. It requires `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `CDP_WALLET_SECRET` in
`~/.hermes/.env`.

Discovery is **native**: onboarding registers the public **CDP Bazaar MCP**
(`search_resources` + `proxy_tool_call`, no auth) under Hermes `mcp_servers` so the agent
calls it as `mcp_bazaar_*`.

## What it provides

- Paid-call tools:
  - `x402_request` — pay a known x402 HTTP URL (402 → sign → retry).
  - `x402_retry_mcp_payment` — pay + retry any native `mcp_*` call that returned
    payment-required (Bazaar services and any paid MCP server in `mcp_servers`).
- Local CDP wallet tools (`local` provider): `cdp_wallet_status`, `cdp_wallet_balance`,
  `cdp_faucet` (testnet only), `cdp_onramp` (buy USDC/ETH with fiat), `cdp_transfer`.
- `hermes x402 ...` CLI (init, wallet, fund, balance, status, spend, payments, reconcile)
- `/x402` in-session slash command
- A bundled `x402-payments` skill for the local CDP wallet flow
- `run_x402_onboarding(config)` — the seam the upstream `hermes setup` flow calls into

## Install

```bash
pip install hermes-x402
# pip plugins are opt-in via config:
# plugins:
#   enabled: [hermes-x402]
```

Configure the provider (`~/.hermes/config.yaml`). Local CDP is the default:

```yaml
x402:
  provider: local
  cdp_account_name: hermes-x402
  network: base-sepolia     # or "base" (mainnet)
```

Put CDP credentials in `~/.hermes/.env` (never in `config.yaml`):

```
CDP_API_KEY_ID=...
CDP_API_KEY_SECRET=...
CDP_WALLET_SECRET=...
```

Fund the wallet: testnet via the `cdp_faucet` tool; mainnet via the `cdp_onramp` tool.
Run `hermes x402 init` once to provision the wallet and register the Bazaar MCP.

`x402.session_budget_usdc` applies to paid x402 HTTP/MCP calls. Direct wallet transfers
through `cdp_transfer` are not x402 payments; USDC transfers are guarded by
`x402.max_price_usdc` unless `override=true`.

## Layout

```
hermes_x402/
├── __init__.py          register(ctx) — wires tools, CLI, slash, skills, hooks
├── cdp/                 local provider: CDP server wallet (client + wallet_ops + signer)
├── coinbase_mcp/        future remote signer plumbing + shared payment client seam
├── mcp_client.py        shared MCP transport + result parsing + McpSessionAdapter
├── mcp_servers.py       ensure_mcp_servers(config) — bazaar discovery/proxy registration
├── config.py            hermes-home-aware paths + plugin config (x402.provider, ...)
├── ledger.py            spend/payment ledger (sqlite)
├── budget.py            pre_tool_call session-budget gate
├── setup_flow.py        run_x402_onboarding(config) — provider selection + provisioning
├── monetize.py          charge for your own MCP tool (server side)
├── facilitator.py       CDP facilitator wiring (monetize side)
├── tools/               request.py + retry_mcp.py + cdp_tools.py + shared _paid.py + schemas
├── cli/                 hermes x402 subcommand tree
└── skills/              x402-payments-local
```

> Paying for **inference** via `provider: x402` is intentionally out of scope for now — it
> needs payment schemes (`upto` / `batch-settlement`) we are not implementing yet. This
> plugin pays for HTTP/MCP **tools** with the `exact` scheme.

## How signing is wired (the one seam)

`x402HttpxClient` (HTTP) and `x402MCPClient` (MCP) both reach the wallet through a single
method — `create_payment_payload(PaymentRequired) -> PaymentPayload`. We implement that in
`coinbase_mcp/payment_client.py::CoinbaseMcpPaymentClient`, which keeps the per-call budget
gates and delegates to the local signer backend in `cdp/signer.py` (exact EVM / EIP-3009;
Permit2 skipped).

Both transports work with no per-transport reshaping. Wallet address/balance reads route
through `wallet.py` into local `cdp/wallet_ops.py`.

The only selectable provider in this release is `local`. Remote Coinbase MCP signing is
future work; unsupported provider values are normalized back to `local`.

## Built on the x402 Python SDK

- `x402.http.clients.httpx.x402HttpxClient` — HTTP 402 -> sign -> retry (signer = us)
- `x402.mcp.x402MCPClient` — MCP payment-required -> sign -> retry (signer = us)
- `x402.mcp.create_payment_wrapper` — charge for your own MCP tools (`monetize.py`)
- `x402.extensions.bazaar` — declare discovery metadata when monetizing (`monetize.py`).
  Client-side discovery is the native CDP Bazaar MCP (registered in `mcp_servers`).
