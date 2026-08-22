# Upstream deliverable (for the Hermes Agent PR)

Thin artifacts to land in [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent).
Everything substantial lives in the `hermes-x402` companion package; Hermes core adds one
setup flag, automatic pip install/upgrade, and a docs page.

## Contents

- `setup_coinbase_flag.md` — exact changes for `hermes_cli/main.py` and `hermes_cli/setup.py`
- `docs/paying-with-x402.md` — short user-facing docs page

> No `plugins/model-providers/x402/` stub. Paying for **inference** via `provider: x402` is
> out of scope (`upto` / batch-settlement schemes).

## Why thin

Hermes merges small PRs. This one is:

- One CLI flag: `hermes setup --coinbase`
- One delegate: pip install/upgrade `hermes-x402`, rediscover plugins, call companion onboarding
- One docs page

## Flow

```text
hermes setup --coinbase
  → pip install -U hermes-x402          # tools_config._pip_install (uv-first)
  → _ensure_plugins_discovered(force=True)
  → hermes_x402.setup_flow.run_x402_onboarding(config)
```

Use `_pip_install` from `hermes_cli/tools_config.py`, not a raw `pip` subprocess.

The companion is a **pip entry-point plugin** (`hermes_agent.plugins`), not a git plugin.
Do not use `hermes plugins install` for it.

Re-running `hermes setup --coinbase` is safe: `-U` picks up companion updates and
onboarding is idempotent (see `setup_coinbase_flag.md`).

## Out of scope for this PR

Wallet provisioning, CDP credentials, Bazaar MCP registration, x402 tools, budgets,
ledger, and CLI all stay in `hermes-x402`.
