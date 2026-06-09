# Adding `hermes setup --coinbase` (minimal upstream change)

> **Status: Proposed — not yet merged into `hermes-agent`.**
> This document describes the changes required to add the `--coinbase` flag to
> `hermes-agent`. Until this PR is merged, users should use `hermes x402 init`
> from the companion CLI directly.

Mirrors the existing `--portal` one-shot path. Two edits, both small.

## 1. `hermes_cli/main.py` — add the flag

In the `setup` subparser (next to `--portal`):

```python
setup_parser.add_argument(
    "--coinbase",
    action="store_true",
    help="One-shot x402 setup: choose a wallet provider, provision the CDP server wallet, "
         "and register the Bazaar MCP server. Skips the rest of the wizard.",
)
```

## 2. `hermes_cli/setup.py` — early-exit branch in `run_setup_wizard`

Next to the existing `--portal` branch:

```python
if bool(getattr(args, "coinbase", False)):
    _run_x402_one_shot(config)
    return
```

And add the delegate (keeps Hermes thin — all logic is in the companion):

```python
def _run_x402_one_shot(config: dict) -> None:
    try:
        from hermes_x402.setup_flow import run_x402_onboarding
    except ImportError:
        print_warning("Install the companion first:  pip install hermes-x402")
        return
    run_x402_onboarding(config)
```

## What `run_x402_onboarding` does (all in the companion)

1. **Provider selection**: fixed to `local` (self-custodial CDP server wallet).

2. **Credential check + wallet provisioning** (local provider): reads
   `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `CDP_WALLET_SECRET` from `~/.hermes/.env`.
   If missing, prints specific instructions and exits cleanly (no crash). If present,
   provisions (or reuses) a named CDP server wallet via the CDP SDK and prints the address.

3. **MCP servers**: registers `bazaar` (discovery + proxy) under `mcp_servers`.
   Any stale `coinbase` entry is removed.

4. **Balance + funding hint**: reads on-chain USDC; if empty, prints `cdp_faucet`
   (testnet) or `cdp_onramp` (mainnet) instructions.

5. **Budgets**: writes `max_price_usdc` + `session_budget_usdc` defaults under `x402:`
   if not already set.

6. **Persist**: calls `save_config()` to write all changes to `~/.hermes/config.yaml`.

## Notes

- No other core files change. The companion owns all wallet/payment logic and registers
  the Bazaar MCP. (No `provider: x402` registration — inference payment is out of scope.)
- Local mode requires CDP credentials (`CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`,
  `CDP_WALLET_SECRET`). These go in `~/.hermes/.env`, never in `config.yaml`.
