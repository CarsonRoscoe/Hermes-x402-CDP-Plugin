# hermes-x402-plugin

> Wallet-funded x402 payments for [Hermes Agent](https://github.com/NousResearch/hermes-agent), built on [x402](https://github.com/x402-foundation/x402), with signing delegated to a Coinbase MCP server.

This repo holds the **companion plugin** — the half of the integration where the magic
lives. It lets a Hermes agent discover and call paid HTTP and MCP services with USDC
micropayments. Payment **signing is delegated to a Coinbase MCP server** (OAuth'd,
custodial wallet) — no key material lives in the agent.

> Paying for **inference** via `provider: x402` is intentionally out of scope for now (it
> needs `upto` / `batch-settlement` schemes we are not implementing yet). The plugin pays
> for HTTP/MCP **tools** with the `exact` scheme.

## Three pieces

1. **Companion plugin (`hermes-x402/`)** — a pip-installable package (`hermes-x402`) with
   all the real logic: the Coinbase MCP connection + payment seam, the two payment tools,
   CLI, onboarding (which registers the Coinbase + Bazaar MCP servers), and monetize. Ships
   via the `hermes_agent.plugins` entry point.
2. **Fake Coinbase MCP (`fake-coinbase-mcp/`)** — a local stdio server implementing the one
   new tool we are asking the Coinbase MCP team to build (`create_payment_payload`), so we
   can develop end to end before the real server exists. It signs with a throwaway local
   key (or a CDP server wallet). **Dev-only; never run in production.**
3. **Upstream thin PR (`upstream/`, later into `hermes-agent`)** — a
   `hermes setup --coinbase` flag delegating to `run_x402_onboarding(config)`, plus a docs
   page. Minimal; the weight stays here. (No model-provider stub: `provider: x402`
   inference is out of scope for now.)

## Repo layout

```
hermes-x402-plugin/
├── examples/          Usage examples (paid HTTP, paid MCP, monetize, discover)
├── hermes-x402/       The distributable plugin package (pip: hermes-x402)
├── fake-coinbase-mcp/ Local dev signer (the contract the real Coinbase MCP must match)
└── upstream/          Thin artifacts for the hermes-agent PR
```

## The signing contract

We define and drive **one** new Coinbase MCP tool; the plugin codes only to it:

- `create_payment_payload` — input: a full x402 `PaymentRequired`; output: a signed
  `PaymentPayload`. The Coinbase MCP selects which `PaymentRequirements` to pay.

Because the x402 SDK already encodes/decodes both transport envelopes around the single
`create_payment_payload` seam, HTTP and MCP payments both work by swapping in this signer —
no per-transport reshaping. Balance/address reuse the Coinbase MCP's existing
`coinbase_balance` / `coinbase_status` tools.

## Quick start (dev, against the fake)

**1. Install Hermes** from the local clone (requires Python 3.11+):

```bash
cd /path/to/hermes-agent
uv venv .venv --python 3.11
uv pip install -e ".[all]"
```

**2. Install the plugin and fake signer into the same venv:**

```bash
cd /path/to/hermes-x402-plugin
uv pip install --python /path/to/hermes-agent/.venv/bin/python \
    -e hermes-x402 -e fake-coinbase-mcp
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
  coinbase_mcp:
    transport: stdio              # uses the fake signer automatically
  max_price_usdc: 0.10
  session_budget_usdc: 5.0
  failure_mode: strict
```

Add your API key to `~/.hermes/.env`:

```bash
echo 'MY_LLM_API_KEY=<your-token>' >> ~/.hermes/.env
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
hermes x402 init      # connects to the fake signer, registers MCP servers
hermes x402 status    # confirm signer connection + wallet balance
hermes                # start chatting
```

For prod, set `x402.coinbase_mcp.transport: remote` + the OAuth token env, pointed at the
hosted Coinbase MCP — the plugin code is unchanged.

## Native MCP servers

Onboarding writes two entries into `mcp_servers:` in `~/.hermes/config.yaml`:

- `coinbase` — the Coinbase MCP (wallet reads + any brokerage tools it exposes).
- `bazaar` — the public CDP Bazaar MCP (`search_resources` + `proxy_tool_call`).

The agent calls these natively (`mcp_*`). The plugin keeps a **separate** internal Coinbase
MCP connection used only for signing payments.

## How a Hermes plugin is wired

See [`hermes-x402/README.md`](hermes-x402/README.md). The package's `register(ctx)`
registers the two agent tools, the `hermes x402` CLI tree, a `/x402` slash command, the
bundled skill, and a budget hook — all through the standard Hermes `PluginContext` API,
with nothing patched into Hermes core.

## License

Apache-2.0. See [LICENSE](LICENSE).
