---
name: run-tests
description: Run the hermes-x402 test suite and verify the plugin is healthy. Use this before and after making changes to confirm nothing is broken.
version: 0.0.1
author: Coinbase
---

# Run Tests: hermes-x402-plugin

All tests live in `hermes-x402/tests/test_plugin.py`. They are fully offline — no network,
no Coinbase credentials, no real USDC. The Coinbase MCP signer is mocked with `monkeypatch`.

## Prerequisites

```bash
pip install -e hermes-x402[dev]
pip install -e fake-coinbase-mcp
```

## Standard verification sequence

Run these three commands after any substantive change:

```bash
# From hermes-x402/
python -m pytest -q                   # must pass: 31 passed
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
#   tools: ['x402_request', 'x402_retry_mcp_payment']
#   cli: ['balance', 'fund', 'init', 'payments', 'reconcile', 'spend', 'status', 'wallet']
#   failure_mode: strict
```

## Linting

```bash
python -m ruff check hermes_x402     # if ruff is in the env
# Or use Cursor's ReadLints tool on any file you edited.
```

## What each test group covers

### Plugin registration
`test_register_wires_all_surfaces` — `register(ctx)` produces exactly the two tools,
one CLI entry, one slash command, one skill, and two hooks. This is the canary for
accidental surface changes.

### Tool input validation
`test_request_returns_json_on_bad_args`, `test_retry_requires_tool_name` — tools return
a JSON error dict (never raise) when required args are missing.

### MCP name resolution
`test_sanitize_matches_hermes`, `test_resolve_server_hyphen_and_uppercase`,
`test_resolve_server_longest_prefix`, `test_resolve_server_unknown_raises`,
`test_retry_errors_when_server_has_no_url` — the sanitizer in `retry_mcp.py` must match
Hermes's own `sanitize_mcp_name_component` exactly. The longest-prefix match handles server
names that contain underscores or hyphens.

### Upstream tool-name recovery
`test_resolve_upstream_name_maps_sanitized_to_real` — `resolve_upstream_name` maps a
sanitized suffix (e.g. `get_sum`) back to the real tool name (`get-sum`) using the
server's `list_tools` response. Dict-shaped and object-shaped tool entries both work.

### McpSessionAdapter / structuredContent
`test_adapter_preserves_structured_content_and_meta` — the adapter must return a shim
with camelCase attribute names (`isError`, `_meta`, `structuredContent`) matching what
the x402 SDK's `convert_mcp_result` reads. Without this, payment-required detection via
`structuredContent` silently fails.

### Shared MCP client parsing
`test_mcp_client_parses_structured_content`, `test_mcp_client_parses_text_json_fallback`,
`test_mcp_client_raises_on_tool_error` — `result_to_dict` prefers `structuredContent`,
falls back to text JSON, and raises a `RuntimeError` on `isError: true`.

### Cap resolution
`test_effective_cap_picks_stricter` — `effective_cap` returns the minimum of the per-call
argument and the config ceiling; zero means uncapped.

### Payment client cap gates
`test_payment_client_caps_before_signing` — refuses before contacting the signer when the
cheapest accept already exceeds the cap (no connection call made).
`test_payment_client_delegates_to_connection` — forwards to the connection and validates
the returned `PaymentPayload`; records `last_min_usdc`.

### Money-safety hardening
`test_failure_mode_defaults_strict` — `failure_mode` is `strict` by default; timeout and
trust-flag defaults are correct.
`test_r4_selected_amount_over_cap_rejected` — the plugin enforces the cap against the
requirement the signer actually selected (not just the cheapest accept).
`test_r4_unverifiable_selected_amount_rejected_strict` — strict mode refuses when the
signer returns a payload with no `accepted.amount`.
`test_r2_run_async_cancels_on_timeout` — `run_async` cancels the background future and
raises `UnknownSettlementError` on timeout, not a generic `TimeoutError`.
`test_r8_budget_hook_fails_closed_on_error` — when the ledger is unreadable, strict mode
blocks the paid call; best-effort mode allows it.
`test_r3_journal_states_roundtrip` — a journal entry transitions `pending → succeeded`;
open entries appear in `journal_open_entries`; after finalization the backlog is empty.
`test_r1_idempotency_replay_without_repay` — a second call with the same `idempotency_key`
returns the cached result without invoking `run` again.
`test_r1_open_attempt_blocks_without_override` — a pending journal entry blocks a second
attempt with the same fingerprint unless `override=True`.
`test_r5_reservation_blocks_concurrent_overshoot` — `journal_begin` raises
`BudgetExceededError` when `spent + reserved + cap > budget` inside a single transaction.

### MCP server registration
`test_ensure_mcp_servers_writes_both` — default config writes a stdio coinbase entry and a
bazaar URL entry.
`test_ensure_mcp_servers_sources_from_passed_config` — remote coinbase config is read from
the in-memory `config_dict`, not re-read from disk.

### Ledger
`test_ledger_roundtrip_strips_query_params` — only `endpoint_host` is persisted (never
the full URL or query params); `session_total` aggregates correctly.

### Budget hook
`test_budget_hook_blocks_over_session_budget` — blocks when already over budget.
`test_budget_hook_blocks_projected_overshoot` — blocks when `spent + cap > budget` even
if not yet over the ceiling.
`test_budget_hook_fails_closed_on_error` — see money-safety section above.

### Config
`test_config_coinbase_mcp_defaults` — HERMES_HOME is isolated to tmp; default
coinbase_mcp values; CAIP-2 string for Base.

## Adding a test

1. Add a `test_*` function to `hermes-x402/tests/test_plugin.py`.
2. The `isolated_home` fixture (in `conftest.py`) runs automatically for every test: it
   sets `HERMES_HOME` to a fresh temp directory and resets the cached Coinbase MCP
   connection so tests cannot bleed state into each other.
3. Mock the signer and network calls with `monkeypatch`. See `test_payment_client_caps_before_signing`
   for a minimal signer mock pattern and `test_ensure_mcp_servers_sources_from_passed_config`
   for a config mock pattern.
4. Run `python -m pytest -q` — the suite should still report 31+ passed with no failures.

## What to check after specific change types

| Change type | Commands to run |
|---|---|
| New or edited tool handler | `pytest -q`, `compileall`, import smoke test |
| Ledger or journal schema change | `pytest -q` (esp. `test_r3_*`, `test_r5_*`); delete `~/.hermes/x402/ledger.sqlite` if testing manually |
| Config key added or renamed | `pytest -q`; update `DEFAULTS` in `config.py` and the relevant test |
| Signer seam change | Update `fake-coinbase-mcp/fake_coinbase_mcp/server.py` and `payment_client.py` together; run `pytest -q` |
| MCP transport / adapter change | `test_adapter_preserves_structured_content_and_meta` is the key canary |
