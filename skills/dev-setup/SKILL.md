---
name: dev-setup
description: Set up the hermes-x402-plugin dev environment from scratch. Use this when asked to install dependencies, configure the CDP wallet, or get the repo ready for development and testing.
version: 0.0.1
author: Coinbase
---

# Dev Setup: hermes-x402-plugin

This repo contains the `hermes-x402` plugin — a self-contained Hermes Agent plugin that
adds x402 micropayments and a self-custodial CDP server wallet. A hosted Remote Coinbase
MCP provider is Coming Soon; all current dev uses the local CDP wallet directly.

## Repo layout

```
hermes-x402-plugin/
├── hermes-x402/        ← the pip-installable Hermes plugin (hermes_x402 package)
│   ├── hermes_x402/    ← plugin source
│   │   ├── cdp/        ← local CDP server-wallet core (client, wallet_ops, signer)
│   │   ├── coinbase_mcp/ ← Coming Soon remote provider plumbing (connection, payment_client)
│   │   └── tools/      ← x402_request, x402_retry_mcp_payment, cdp_* wallet tools
│   └── tests/          ← pytest suite (all offline, no network calls)
├── examples/           ← runnable usage examples
├── docs/               ← interface reference docs
└── skills/             ← this directory; Cursor/Claude Code agent guidance
```

## Install

Run from the repo root:

```bash
pip install -e "hermes-x402[dev]"    # plugin + pytest + ruff
```

Verify:

```bash
python -c "import hermes_x402; print('plugin OK')"
```

## CDP credentials

The local provider uses a CDP server wallet. Add credentials to `~/.hermes/.env`:

```
CDP_API_KEY_ID=...
CDP_API_KEY_SECRET=...
CDP_WALLET_SECRET=...
```

Optional overrides:
```
CDP_ACCOUNT_NAME=hermes-x402   # wallet name in CDP (default: hermes-x402)
```

The wallet is provisioned via `cdp.evm.get_or_create_account` — idempotent, the same named
wallet is reused on restart.

## Configure Hermes

Add to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-x402

x402:
  provider: local       # self-custodial CDP server wallet (default)
  network: base-sepolia # or "base" for mainnet
  max_price_usdc: 1.0
  session_budget_usdc: 10.0
  failure_mode: strict
```

Then run:

```bash
hermes x402 init     # provisions wallet + registers Bazaar MCP
hermes x402 status   # verify wallet address + balance
```

## Key architecture facts

**Wallet provider status (`x402.provider`):**
- `local` (default): self-custodial CDP server wallet via the CDP SDK in-process.
  `hermes_x402.cdp` is the single source of truth for all CDP logic.
- `coinbase_mcp` (Coming Soon): remote hosted Coinbase MCP (OAuth). It is not selectable
  today; unsupported provider values normalize back to `local`.

**Paid-call tools (always present):**
- `x402_request` — paid HTTP (`tools/request.py`)
- `x402_retry_mcp_payment` — reactive paid MCP (`tools/retry_mcp.py`)

**Local CDP wallet tools (visible in `local` mode, gated by `check_fn`):**
- `cdp_wallet_status`, `cdp_wallet_balance`, `cdp_faucet`, `cdp_onramp`,
  `cdp_transfer`, `cdp_payments`

**Shared plumbing:**
- `coinbase_mcp/payment_client.py` — signer seam + cap gates (local CDP today, remote later)
- `tools/_paid.py` — cap resolution, error mapping, journal + idempotency
- `ledger.py` — SQLite spend ledger + payment journal
- `mcp_client.py` — MCP transport, `McpSessionAdapter`, `with_timeout`

**Default posture is fail-closed** (`failure_mode: strict`). Budget, cap, and journal
guards refuse to let money move when they cannot be verified.

## Common failure modes

**`RuntimeError: CDP credentials not set`**
Add `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `CDP_WALLET_SECRET` to `~/.hermes/.env`,
then re-run `hermes x402 init`.

**`ModuleNotFoundError: No module named 'mcp'`**
Extras not installed. Run `pip install -e "hermes-x402[dev]"`.

**Ledger or journal errors during tests**
The SQLite database is isolated per-test via the `isolated_home` fixture in
`tests/conftest.py`. If you see state leaking between tests, check the fixture is in place.
