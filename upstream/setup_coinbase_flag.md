# Adding `hermes setup --coinbase` (minimal upstream change)

> **Status: Proposed — not yet merged into `hermes-agent`.**
> Until this PR lands, users can run `hermes x402 init` from the companion CLI directly
> (after `pip install hermes-x402`).

Mirrors the existing `--portal` one-shot path: one flag, early exit in `run_setup_wizard`,
delegate to the companion. Unlike `--portal`, this path **pip-installs or upgrades
`hermes-x402` before importing it** — the companion is an external PyPI package, not code
in Hermes core.

Do **not** use `hermes plugins install` — that git-clones into `~/.hermes/plugins/`.
`hermes-x402` registers via the `hermes_agent.plugins` entry-point group (see
`hermes_cli/plugins.py`).

---

## 1. `hermes_cli/main.py` — add the flag

In the `setup` subparser (next to `--portal`):

```python
setup_parser.add_argument(
    "--coinbase",
    action="store_true",
    help="One-shot x402 setup: install/upgrade hermes-x402, provision the CDP server "
         "wallet, and register the Bazaar MCP server. Skips the rest of the wizard.",
)
```

---

## 2. `hermes_cli/setup.py` — early-exit branch in `run_setup_wizard`

Next to the existing `--portal` branch:

```python
if bool(getattr(args, "coinbase", False)):
    _run_coinbase_one_shot(config)
    return
```

Add the delegate:

```python
import sys

_COMPANION_PIP = "hermes-x402"


def _run_coinbase_one_shot(config: dict) -> None:
    """Install/upgrade hermes-x402 and run companion onboarding."""
    from hermes_cli.tools_config import _pip_install

    print_info(f"Installing/upgrading {_COMPANION_PIP}...")
    result = _pip_install(["-U", _COMPANION_PIP])
    if result.returncode != 0:
        stderr = (getattr(result, "stderr", None) or "").strip()
        print_warning(f"Could not install {_COMPANION_PIP} automatically.")
        if stderr:
            print_info(stderr)
        print_info(
            f"Try manually: {sys.executable} -m pip install -U {_COMPANION_PIP}"
        )
        return

    try:
        from hermes_cli.plugins import _ensure_plugins_discovered

        _ensure_plugins_discovered(force=True)
    except Exception as exc:
        print_warning(f"Plugin rediscovery skipped: {exc}")

    try:
        from hermes_x402.setup_flow import run_x402_onboarding
    except ImportError:
        print_warning(
            f"{_COMPANION_PIP} installed but hermes_x402.setup_flow not found. "
            "Check the package version."
        )
        return

    run_x402_onboarding(config)
    print_info("Restart Hermes if mcp_servers or tool lists changed.")
```

`_pip_install` (in `hermes_cli/tools_config.py`) tries `uv pip install` first, then
`python -m pip`, and bootstraps pip via `ensurepip` when the venv has no pip.

---

## What `run_x402_onboarding` does (companion — not Hermes core)

Implemented in `hermes_x402/setup_flow.py`. Called after pip install succeeds.

1. **Provider** — sets `x402.provider` to `local` (CDP server wallet).
2. **Wallet** — checks `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `CDP_WALLET_SECRET` in
   `~/.hermes/.env`. If present, provisions or reuses the named CDP server wallet and
   prints the address. If missing, prints setup instructions and continues without crashing.
3. **MCP servers** — registers the CDP Bazaar MCP under `mcp_servers`; removes stale
   `coinbase` signer entries.
4. **Balance** — reads USDC balance; prints faucet (testnet) or onramp (mainnet) hints if empty.
5. **Budgets** — `setdefault` for `max_price_usdc` and `session_budget_usdc` under `x402:`.
6. **Plugin enable** — adds `hermes-x402` to `plugins.enabled` if not already there.
7. **Persist** — saves config via `hermes_cli.config.save_config`.

Onboarding is idempotent: re-running `hermes setup --coinbase` upgrades the package and
re-applies any missing config steps without clobbering existing values.

---

## Hermes core change summary

| File | Change |
|------|--------|
| `hermes_cli/main.py` | Add `--coinbase` flag to `setup` subparser |
| `hermes_cli/setup.py` | Early-exit → `_run_coinbase_one_shot` |
| docs (see `upstream/docs/paying-with-x402.md`) | User-facing quick start |

No other core files change.

---

## Notes

- **Inference x402** (`provider: x402`) is out of scope — no model-provider stub in this PR.
- **CDP credentials** go in `~/.hermes/.env`, never in `config.yaml`.
- **Restart Hermes** after setup when `mcp_servers` changed so tool descriptions update.
