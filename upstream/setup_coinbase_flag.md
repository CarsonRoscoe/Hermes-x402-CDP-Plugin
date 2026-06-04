# Adding `hermes setup --coinbase` (minimal upstream change)

Mirrors the existing `--portal` one-shot path. Two edits, both small.

## 1. `hermes_cli/main.py` — add the flag

In the `setup` subparser (next to `--portal`):

```python
setup_parser.add_argument(
    "--coinbase",
    action="store_true",
    help="One-shot x402 setup: connect the Coinbase MCP signer and register the "
         "Coinbase/Bazaar MCP servers. Skips the rest of the wizard.",
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

## Notes

- No other core files change. The companion owns the Coinbase MCP connection, the payment
  tools, and registers the Coinbase/Bazaar MCP servers under `mcp_servers`. (No
  `provider: x402` registration — inference payment is out of scope for now.)
- Signing is delegated to the Coinbase MCP (OAuth in prod; a local stdio fake in dev), so
  no CDP keys are required in the agent. The companion reads the wallet address/balance via
  the Coinbase MCP's existing `coinbase_status` / `coinbase_balance` tools.
