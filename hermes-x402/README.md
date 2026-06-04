# hermes-x402

> The companion plugin. x402 micropayments for [Hermes Agent](https://github.com/NousResearch/hermes-agent), with signing delegated to a Coinbase MCP server.

This is the distributable Python package (`hermes-x402`, module `hermes_x402`). It is
both a **pip-installable Hermes plugin** (via the `hermes_agent.plugins` entry point)
and a **drop-in plugin directory** (copy `hermes_x402/` into `~/.hermes/plugins/`).

Payment signing is **not** done in the plugin. The plugin delegates to a Coinbase MCP
server (OAuth'd, remote in prod; the local [`fake-coinbase-mcp`](../fake-coinbase-mcp) in
dev) via one tool — `create_payment_payload` (PaymentRequired -> PaymentPayload). No key
material lives in the agent.

Discovery and paid-MCP calls are **native**: onboarding registers both the **Coinbase MCP**
(signing + wallet reads) and the public **CDP Bazaar MCP** (`search_resources` +
`proxy_tool_call`, no auth) under Hermes `mcp_servers`, so the agent calls them as `mcp_*`
tools. The plugin keeps a separate internal Coinbase MCP connection only for signing.

## What it provides

- Two agent tools:
  - `x402_request` — pay a known x402 HTTP URL (402 → sign → retry).
  - `x402_retry_mcp_payment` — pay + retry any native `mcp_*` call that returned
    payment-required (Bazaar services and any paid MCP server in `mcp_servers`).
- `hermes x402 ...` CLI (init, wallet, fund, balance, status, spend, payments)
- `/x402` in-session slash command
- A bundled `x402-payments` skill that teaches the agent how to use the tools
- `run_x402_onboarding(config)` — the seam the upstream `hermes setup --coinbase` flag calls into

## Install

```bash
pip install hermes-x402
hermes plugins enable hermes-x402     # plugins are opt-in
```

Configure the signer (`~/.hermes/config.yaml`). Dev (local fake) is the default:

```yaml
x402:
  coinbase_mcp:
    transport: stdio            # or "remote"
    command: fake-coinbase-mcp  # dev: pip install -e fake-coinbase-mcp
    # remote:
    # url: https://mcp.coinbase.example/mcp
    # auth_token_env: COINBASE_MCP_TOKEN
```

## Layout

```
hermes_x402/
├── __init__.py          register(ctx) — wires tools, CLI, slash, skills, hooks
├── coinbase_mcp/        connection + CoinbaseMcpPaymentClient (signer seam) + wallet facade
├── mcp_client.py        shared MCP transport + result parsing + McpSessionAdapter
├── mcp_servers.py       ensure_mcp_servers(config) — registers coinbase + bazaar
├── config.py            hermes-home-aware paths + plugin config (x402.coinbase_mcp)
├── ledger.py            spend/payment ledger (sqlite)
├── budget.py            pre_tool_call session-budget gate
├── setup_flow.py        run_x402_onboarding(config)
├── monetize.py          charge for your own MCP tool (server side)
├── facilitator.py       CDP facilitator wiring (monetize side)
├── tools/               request.py + retry_mcp.py + shared _paid.py + schemas
├── cli/                 hermes x402 subcommand tree
└── skills/              bundled plugin skills
```

> Paying for **inference** via `provider: x402` is intentionally out of scope for now — it
> needs payment schemes (`upto` / `batch-settlement`) we are not implementing yet. This
> plugin pays for HTTP/MCP **tools** with the `exact` scheme.

## How signing is wired (the one seam)

`x402HttpxClient` (HTTP) and `x402MCPClient` (MCP) both reach the wallet through a single
method — `create_payment_payload(PaymentRequired) -> PaymentPayload`. We implement that in
`coinbase_mcp/payment_client.py::CoinbaseMcpPaymentClient`, which forwards the full
`PaymentRequired` to the Coinbase MCP's `create_payment_payload` tool (the MCP picks which
requirement to pay) and returns the signed `PaymentPayload`. Both transports therefore work
with no per-transport reshaping.

Balance/address reads reuse the Coinbase MCP's existing tools (`coinbase_balance`,
`coinbase_status`) via `coinbase_mcp/wallet.py`.

## Built on the x402 Python SDK

- `x402.http.clients.httpx.x402HttpxClient` — HTTP 402 -> sign -> retry (signer = us)
- `x402.mcp.x402MCPClient` — MCP payment-required -> sign -> retry (signer = us)
- `x402.mcp.create_payment_wrapper` — charge for your own MCP tools (`monetize.py`)
- `x402.extensions.bazaar` — declare discovery metadata when monetizing (`monetize.py`).
  Client-side discovery is the native CDP Bazaar MCP (registered in `mcp_servers`).
