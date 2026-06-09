# hermes-x402-plugin

> Wallet-funded x402 payments for [Hermes Agent](https://github.com/NousResearch/hermes-agent), built on [x402](https://github.com/x402-foundation/x402), with a self-custodial CDP server wallet.

This repo holds the **companion plugin** — the half of the integration where the magic
lives. It lets a Hermes agent discover and call paid HTTP and MCP services with USDC
micropayments. The default **`local` provider** runs a **self-custodial CDP server wallet**
in-process (via the CDP SDK) and adds native `cdp_*` wallet-management tools. A future
**`coinbase_mcp` provider** (remote hosted signer) is **Coming Soon**; exactly one provider
is active at a time, chosen by `x402.provider`.

> Paying for **inference** via `provider: x402` is intentionally out of scope for now (it
> needs `upto` / `batch-settlement` schemes we are not implementing yet). The plugin pays
> for HTTP/MCP **tools** with the `exact` scheme.

## Two pieces

1. **Companion plugin (`hermes-x402/`)** — a pip-installable package (`hermes-x402`) with
   all the real logic: the local CDP wallet core (`cdp/`), the payment seam, the two payment
   tools + native `cdp_*` wallet tools, CLI, onboarding (which registers the Bazaar MCP), and
   monetize. Ships via the `hermes_agent.plugins` entry point.
2. **Upstream thin PR (`upstream/`, later into `hermes-agent`)** — a `hermes setup --coinbase`
   flag delegating to `run_x402_onboarding(config)`, plus a docs page. Minimal; the weight
   stays here. (No model-provider stub: `provider: x402` inference is out of scope for now.)

## Repo layout

```
hermes-x402-plugin/
├── docs/              Interface reference (wallet tool interface comparison)
├── examples/          Runnable usage examples
├── hermes-x402/       The distributable plugin package (pip: hermes-x402)
├── skills/            Dev-setup and run-tests guidance for Claude Code / Cursor
└── upstream/          Thin artifacts for the hermes-agent PR (hermes setup --coinbase)
```

## The signing seam

Both transports reach the wallet through **one** method — `create_payment_payload(PaymentRequired)
-> PaymentPayload` — so HTTP and MCP payments work by swapping the signer with no
per-transport reshaping. The per-call budget gates live in the payment client; signing is
delegated to the active provider:

- **local** (default): `cdp/signer.py` signs in-process via the CDP SDK (`EvmLocalAccount`),
  selecting an exact EVM (EIP-3009) requirement and skipping Permit2.
- **coinbase_mcp** (Coming Soon): forwards `PaymentRequired` to the Coinbase MCP's
  `create_payment_payload` tool, which selects which `PaymentRequirements` to pay.

Wallet address/balance route through a provider-aware facade (local: the CDP SDK; remote:
the Coinbase MCP's `coinbase_balance` / `coinbase_status`).

## Quick start (dev, against the fake)

**1. Install Hermes** from the local clone (requires Python 3.11+):

```bash
cd /path/to/hermes-agent
uv venv .venv --python 3.11
uv pip install -e ".[all]"
```

**2. Install the plugin into the same venv:**

```bash
cd /path/to/hermes-x402-plugin
uv pip install --python /path/to/hermes-agent/.venv/bin/python \
    -e hermes-x402
```

**3. Activate the venv** (required every new terminal session, or add to `~/.zshrc`):

```bash
source /path/to/hermes-agent/.venv/bin/activate

# Or add a permanent alias:
echo "alias hermes='/path/to/hermes-agent/.venv/bin/hermes'" >> ~/.zshrc
```

**4. Configure** `~/.hermes/config.yaml`:

```yaml
model: my-provider/claude-sonnet-4-6   # prefix matches the providers: key below

providers:
  my-provider:
    base_url: https://your-llm-gateway/
    key_env: MY_LLM_API_KEY

x402:
  provider: local               # self-custodial CDP server wallet (default)
  network: base-sepolia
  max_price_usdc: 0.10
  session_budget_usdc: 5.0
  failure_mode: strict
```

Add your API key and CDP credentials to `~/.hermes/.env`:

```bash
echo 'MY_LLM_API_KEY=<your-token>' >> ~/.hermes/.env
echo 'CDP_API_KEY_ID=<...>'        >> ~/.hermes/.env
echo 'CDP_API_KEY_SECRET=<...>'    >> ~/.hermes/.env
echo 'CDP_WALLET_SECRET=<...>'     >> ~/.hermes/.env
```

**5. Enable the plugin** in `~/.hermes/config.yaml`:

> `hermes plugins enable` only works for directory-based plugins. Pip-installed plugins
> must be listed in config manually.

```yaml
plugins:
  enabled:
    - hermes-x402
```

**6. Onboard and run:**

```bash
hermes x402 init      # provisions the CDP wallet, registers the Bazaar MCP
hermes x402 status    # confirm provider + wallet + balance
hermes                # start chatting (testnet funds: ask it to call cdp_faucet)
```

The `coinbase_mcp` provider is Coming Soon; the plugin code already supports it behind
`x402.provider: coinbase_mcp`.

## Native MCP servers

Onboarding writes into `mcp_servers:` in `~/.hermes/config.yaml`:

- `bazaar` — the public CDP Bazaar MCP (`search_resources` + `proxy_tool_call`). Always present.
- `coinbase` — the Coinbase MCP signer. Only present in the `coinbase_mcp` provider; removed
  in `local` mode (where the native `cdp_*` tools manage the wallet instead).

The agent calls these natively (`mcp_*`).

## How a Hermes plugin is wired

See [`hermes-x402/README.md`](hermes-x402/README.md). The package's `register(ctx)`
registers the two agent tools, the `hermes x402` CLI tree, a `/x402` slash command, the
bundled skill, and a budget hook — all through the standard Hermes `PluginContext` API,
with nothing patched into Hermes core.

## License

Apache-2.0. See [LICENSE](LICENSE).
