---
name: run-tests
description: Run the hermes-x402 test suite and verify the plugin is healthy. Use this before and after making changes to confirm nothing is broken.
version: 0.0.1
author: Coinbase
---

# Run Tests: hermes-x402-plugin

All tests live in `hermes-x402/tests/test_plugin.py`. They are fully offline — no network,
no CDP credentials, no real USDC. CDP wallet calls and signer calls are mocked with
`monkeypatch`.

## Prerequisites

```bash
pip install -e "hermes-x402[dev]"
```

## Standard verification sequence

Run these after any substantive change:

```bash
# From hermes-x402/
python -m pytest -q                   # all must pass
python -m compileall -q hermes_x402   # must produce no output
```

Then check the import surface is intact:

```bash
python -c "
import hermes_x402.tools as t, hermes_x402.cli as c, hermes_x402.config as cfg
print('tools:', [s.name for s in t.TOOLS])
print('cli:', sorted(c._DISPATCH))
print('failure_mode:', cfg.failure_mode())
"
# Expected:
#   tools: ['x402_request', 'x402_retry_mcp_payment', 'cdp_wallet_status', ...]
#   cli: ['balance', 'fund', 'init', 'payments', 'reconcile', 'spend', 'status', 'wallet']
#   failure_mode: strict
```

## Linting

```bash
python -m ruff check hermes_x402     # if ruff is in the env
```

## What each test group covers

### Plugin registration
`test_register_wires_all_surfaces` — `register(ctx)` produces all tools (x402_* + cdp_*),
one CLI entry, one slash command, one skill, and two hooks.

### Tool input validation
`test_request_returns_json_on_bad_args`, `test_retry_requires_tool_name` — tools return
a JSON error dict (never raise) when required args are missing.

### MCP name resolution
`test_sanitize_matches_hermes`, `test_resolve_server_*` — the sanitizer in `retry_mcp.py`
matches Hermes's own `sanitize_mcp_name_component` exactly. Longest-prefix match handles
server names with underscores or hyphens.

### Upstream tool-name recovery
`test_resolve_upstream_name_maps_sanitized_to_real` — maps sanitized suffix back to the
real tool name using `list_tools`. Dict and object tool entries both work.

### McpSessionAdapter / structuredContent
`test_adapter_preserves_structured_content_and_meta` — the adapter returns a shim with
camelCase attributes matching what the x402 SDK's `convert_mcp_result` reads.

### Shared MCP client parsing
`test_mcp_client_*` — `result_to_dict` prefers `structuredContent`, falls back to text
JSON, and raises on `isError: true`.

### Cap resolution
`test_effective_cap_picks_stricter` — `effective_cap` returns the min of the per-call
argument and config ceiling.

### Payment client cap gate
`test_payment_client_caps_before_signing` — refuses before contacting the signer when the
cheapest accept exceeds the cap.

### Money-safety hardening
`test_failure_mode_defaults_strict` — failure_mode, timeout, trust-flag defaults are correct.
`test_r2_run_async_cancels_on_timeout` — `run_async` raises `UnknownSettlementError` on timeout.
`test_r8_budget_hook_fails_closed_on_error` — strict mode blocks when the ledger is unreadable.
`test_r3_journal_states_roundtrip` — journal entry transitions pending → succeeded; backlog clears.
`test_r1_idempotency_replay_without_repay` — same `idempotency_key` returns cached result without re-running.
`test_r1_open_attempt_blocks_without_override` — pending fingerprint blocks a second attempt.
`test_r5_reservation_blocks_concurrent_overshoot` — `journal_begin` raises `BudgetExceededError` when spend + reserved + cap > budget.

### MCP server registration
`test_ensure_mcp_servers_local_provider_bazaar_only` — local mode registers only bazaar.
`test_ensure_mcp_servers_removes_stale_coinbase_in_local_mode` — stale coinbase entry is removed.

### Onboarding flow
`test_onboarding_*` — missing creds print instructions without raising; non-interactive
defaults to local; remote choice falls back to local with "Coming Soon" message; provider
and budgets are persisted.

### Local CDP tool guards
`test_cdp_faucet_rejects_mainnet` — mainnet returns a clear error before CDP calls.
`test_cdp_transfer_over_cap_refused` — USDC amount > cap is refused.
`test_cdp_payments_shape` — returns `{payments[], count, total_usdc}` with correct fields.
`test_cdp_wallet_balance_asset_filter` — `asset=` filters `balances[]` to one entry.

### Provider config helpers
`test_normalize_provider_handles_edge_cases` — case-insensitive, unknown → local, None → local.
`test_is_testnet_classification` — sepolia/hoodi = testnet; base/ethereum = mainnet.
`test_cdp_tools_check_fn_is_local_provider` — all 6 `cdp_*` tools gated by `is_local_provider`.

### Ledger
`test_ledger_roundtrip_strips_query_params` — only `endpoint_host` persisted; `session_total` aggregates correctly.

### Budget hook
`test_budget_hook_blocks_over_session_budget` — blocks when over budget.
`test_budget_hook_blocks_projected_overshoot` — blocks when `spent + cap > budget`.

### Config
`test_config_data_dir_and_caip2` — `HERMES_HOME` is isolated to tmp; CAIP-2 string for Base.

## Adding a test

1. Add a `test_*` function to `hermes-x402/tests/test_plugin.py`.
2. The `isolated_home` fixture runs automatically: sets `HERMES_HOME` to a fresh temp dir
   and resets the CDP Wallet singleton (`cdp_client.wallet._signer = None`, etc.).
3. Mock CDP wallet calls with `monkeypatch.setattr("hermes_x402.cdp.wallet_ops.*", ...)`.
4. Run `python -m pytest -q` — suite must still report all passed with no failures.

## What to check after specific change types

| Change type | Commands to run |
|---|---|
| New or edited tool handler | `pytest -q`, `compileall`, import smoke test |
| Ledger or journal schema change | `pytest -q` (esp. `test_r3_*`, `test_r5_*`); delete `~/.hermes/x402/ledger.sqlite` if testing manually |
| Config key added or renamed | `pytest -q`; update `DEFAULTS` in `config.py` and the relevant test |
| CDP wallet / signing change | Edit `hermes_x402/cdp/` and `payment_client.py` together; run `pytest -q` |
| MCP transport / adapter change | `test_adapter_preserves_structured_content_and_meta` is the key canary |
