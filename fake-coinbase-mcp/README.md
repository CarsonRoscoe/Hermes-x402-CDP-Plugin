# fake-coinbase-mcp

A local, stdio **fake** of the Coinbase MCP x402 signer, so the `hermes-x402` plugin can be
developed end-to-end before the real (remote, OAuth'd) Coinbase MCP exists.

It speaks JSON-RPC over stdin/stdout (MCP protocol `2025-06-18`, like the hosted
`coinbase-mcp`) and implements:

- `create_payment_payload` — **the one new tool we are asking the Coinbase MCP team to
  build.** Input: a full x402 `PaymentRequired`. It selects a `PaymentRequirements` it can
  sign and returns a signed `PaymentPayload` (EIP-3009 `TransferWithAuthorization`).
- `coinbase_balance`, `coinbase_status` — matching stubs of the hosted server's existing
  reads, so the plugin's wallet facade has parity in dev.

## Signing backend

- Default: a local `eth-account` key (zero setup), persisted at
  `~/.hermes-x402-fake/wallet.json` (override with `FAKE_COINBASE_MCP_WALLET`).
- When `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET` / `CDP_WALLET_SECRET` are set and
  `pip install fake-coinbase-mcp[cdp]`, it signs via a CDP server wallet.

## Run

```bash
pip install -e .            # from this directory
fake-coinbase-mcp           # speaks MCP over stdio
```

Point the plugin at it (`~/.hermes/config.yaml`):

```yaml
x402:
  coinbase_mcp:
    transport: stdio
    command: fake-coinbase-mcp
```

> This package is NOT shipped with the plugin and must never run in production — it holds a
> signing key. It exists only to unblock development against the agreed contract.
