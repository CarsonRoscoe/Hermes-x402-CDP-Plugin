---
name: dev-setup
description: Set up the hermes-x402-plugin dev environment from scratch. Use this when asked to install dependencies, configure the local fake signer, or get the repo ready for development and testing.
version: 0.0.1
author: Coinbase
---

# Dev Setup: hermes-x402-plugin

This repo contains a Hermes Agent plugin (`hermes-x402/`) and a local dev stub of the
Coinbase MCP signing server (`fake-coinbase-mcp/`). All tests and local development use
the stub — no cloud credentials, no real USDC.

## Repo layout

```
hermes-x402-plugin/
├── hermes-x402/        ← the pip-installable Hermes plugin (hermes_x402 package)
│   ├── hermes_x402/    ← plugin source
│   └── tests/          ← pytest suite (31 tests, all offline)
├── fake-coinbase-mcp/  ← local dev signer stub (fake_coinbase_mcp package)
│   └── fake_coinbase_mcp/
│       ├── server.py   ← stdio JSON-RPC server implementing create_payment_payload
│       └── signer.py   ← local eth-account key (or CDP if env is set)
├── examples/           ← runnable usage examples
└── skills/             ← this directory; Cursor/Claude Code agent guidance
```

## Install

Run from the repo root:

```bash
pip install -e hermes-x402
pip install -e fake-coinbase-mcp
pip install -e "hermes-x402[dev]"    # adds pytest and ruff
```

Verify:

```bash
python -c "import hermes_x402; print('plugin OK')"
python -c "import fake_coinbase_mcp; print('fake signer OK')"
```

## Key architecture facts to know before editing

**The plugin never signs payments itself.** Signing is delegated to the Coinbase MCP
server via `CoinbaseMcpPaymentClient.create_payment_payload`. In dev, the fake signer
(`fake-coinbase-mcp`) plays that role over stdio. In production it will be a remote
OAuth'd Coinbase MCP. The plugin has no dependency on `eth_account`, `cdp`, or any key
material.

**Two tools only:**
- `x402_request` — paid HTTP (`hermes_x402/tools/request.py`)
- `x402_retry_mcp_payment` — reactive paid MCP (`hermes_x402/tools/retry_mcp.py`)

**Shared plumbing:**
- `hermes_x402/mcp_client.py` — MCP transport, `McpSessionAdapter`, `with_timeout`
- `hermes_x402/tools/_paid.py` — cap resolution, error mapping, journal + idempotency
- `hermes_x402/coinbase_mcp/payment_client.py` — signer seam + cap gates
- `hermes_x402/ledger.py` — SQLite spend ledger + payment journal

**Default posture is fail-closed** (`failure_mode: strict` in config). Budget, cap, and
journal guards refuse to let money move when they cannot be verified.

## Enable the plugin

Hermes does not auto-load pip entry-point plugins. Add the plugin to the `plugins.enabled`
list in `~/.hermes/config.yaml` — `hermes plugins enable` only works for directory-based
plugins, not pip-installed ones.

## Configure Hermes

Add to `~/.hermes/config.yaml`. The `plugins.enabled` entry is required — Hermes does not
auto-load pip entry-point plugins, and `hermes plugins enable` only works for directory-
based plugins, not pip-installed ones:

```yaml
plugins:
  enabled:
    - hermes-x402

x402:
  coinbase_mcp:
    transport: stdio
    # command is resolved automatically to the venv-local binary; leave empty.
  max_price_usdc: 1.0
  session_budget_usdc: 10.0
  failure_mode: strict
```

The fake signer uses a **CDP server wallet** — no local key. CDP credentials are required:

```bash
export CDP_API_KEY_ID=...
export CDP_API_KEY_SECRET=...
export CDP_WALLET_SECRET=...
# optional:
export CDP_ACCOUNT_NAME=hermes-x402   # wallet name in CDP (default: hermes-x402)
export CDP_NETWORK=base-sepolia       # network (default: base-sepolia)
```

The wallet is provisioned via `cdp.evm.get_or_create_account` — idempotent, the same named
wallet is reused on restart.

## Fake signer tools exposed

| Tool | Purpose |
|---|---|
| `create_payment_payload` | Sign an x402 PaymentRequired; return a signed PaymentPayload |
| `coinbase_balance` | Real on-chain ETH + USDC balance via CDP `list_token_balances` |
| `coinbase_status` | CDP wallet address + account name |
| `faucet_eth` | Request 0.0001 testnet ETH from CDP faucet (base-sepolia only; 1000/day) |
| `faucet_usdc` | Request 1 testnet USDC from CDP faucet (base-sepolia only; 10/day) |

## Changing the signer contract

If you need to update the `create_payment_payload` interface, edit:
1. `fake-coinbase-mcp/fake_coinbase_mcp/server.py` — the tool definition and dispatch
2. `fake-coinbase-mcp/fake_coinbase_mcp/signer.py` — the signing logic
3. `hermes-x402/hermes_x402/coinbase_mcp/payment_client.py` — the plugin's client side

Do **not** add signing logic anywhere else in the plugin.

## Common failure modes

**`fake-coinbase-mcp: command not found`**
The entry point was not installed. Run `pip install -e fake-coinbase-mcp` from the repo
root; confirm with `which fake-coinbase-mcp`.

**`ModuleNotFoundError: No module named 'mcp'`**
The plugin's extras were not installed. Run `pip install -e "hermes-x402[dev]"`. If there
is a conflicting system-wide `x402` install, force reinstall:
`pip install --force-reinstall -e hermes-x402`.

**Ledger or journal errors during tests**
The SQLite database is created under `HERMES_HOME`, which each test isolates to a temp
directory via the `isolated_home` fixture in `tests/conftest.py`. If you see persistent
state leaking between tests, check that `conftest.py` is in place.
