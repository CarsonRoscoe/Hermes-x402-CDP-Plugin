# Wallet & x402 tool interfaces

This document catalogs the wallet/payment tool surfaces relevant to the Hermes x402
integration, and proposes the interface we want a wallet provider to converge on.

There are three implementations that exist today, plus one proposed interface:

1. **Coinbase MCP** — the internal hosted brokerage MCP (`c3/coinbase-mcp`).
2. **Fake Coinbase MCP** — our local dev stand-in for the future remote signer.
3. **Local CDP server-wallet tools** — the `cdp_*` tools the plugin ships today (default provider).
4. **x402 Hermes Wallet Interface** — *our ask*: the canonical tool set an x402-paying Hermes
   agent actually needs, which both the local and remote providers should implement.

> Provider model: the plugin selects exactly one wallet provider via `x402.provider`
> (`local` — implemented; `coinbase_mcp` — coming soon). Tool 4 is the logical contract both
> providers should expose; each provider maps it to its own tool-name prefix (`cdp_*` for
> local, `mcp_coinbase_*` for remote).

---

## 1. Coinbase MCP (internal brokerage MCP)

A **retail brokerage** product: a pure OAuth resource server that validates Coinbase Account
Tokens (CATs) and forwards tool calls to the Advanced Trade (RAT) API. Its surface is the
`coinbase-cli` OpenAPI spec — portfolios, orders, market data, and account ops. It does
**not** sign x402 payments today.

| Tool | Inputs | Outputs | Usage |
|---|---|---|---|
| `portfolios_list` | type? | portfolios[] | List the user's portfolios |
| `portfolios_create` | name | portfolio | Create a portfolio |
| `portfolios_get` | portfolio_id, currency? | balances + positions | Portfolio breakdown |
| `portfolios_edit` | portfolio_id, name | portfolio | Rename a portfolio |
| `portfolios_delete` | portfolio_id | ok | Delete a portfolio |
| `orders_list` | status?, product_ids?, side?, dates?, paging | orders[] | List orders |
| `orders_create` | product_id, side, type, size, price?, … | order | Place market/limit/stop-limit order |
| `orders_preview` | product_id, side, type, size, price? | fees, fill estimate | Estimate before executing |
| `orders_edit` | order_id, base_size?, limit_price? | order | Atomically edit an open order |
| `orders_get` | order_id | order | Get one order |
| `orders_cancel` | order_ids[] | results | Cancel orders |
| `orders_close_position` | product_id, size? | order | Close a position (full/partial) |
| `orders_fills` | product_id?, order_ids?, dates?, paging | fills[] | List trade fills |
| `products_list` | type?, product_ids?, paging | products[] | List tradeable products |
| `products_get` | product_id | product | Get one product |
| `products_ticker` | product_id, limit? | ticker | Live ticker (streamable) |
| `products_book` | product_id, limit? | order book | Order book depth (streamable) |
| `products_candles` | product_id, start, end, granularity | candles[] | OHLCV candles (streamable) |
| `products_best_bid_ask` | product_ids? | quotes[] | Best bid/ask across products |
| `balance` | — | account balance | Brokerage account balance |
| `transfer` | from, to, amount, currency | result | Move funds between portfolios |
| `convert_quote` | from, to, amount | quote | Quote a stablecoin/asset conversion |
| `convert_execute` | quote_id | result | Execute a convert quote |
| `convert_get` | quote_id | quote status | Get a convert quote's status |
| `fees` | — | fee tier | Get the account's fee tier |

Auth: OAuth/CAT (user-level). Transport: remote HTTP `/mcp` (JSON-RPC 2.0).

---

## 2. Fake Coinbase MCP (dev stand-in)

A minimal stdio MCP that implements only what x402 needs, so we can develop end-to-end
before the real remote signer exists. Now a thin wrapper over the shared `hermes_x402.cdp`
core; retained as the dev stand-in for the `coinbase_mcp` provider.

| Tool | Inputs | Outputs | Usage |
|---|---|---|---|
| `create_payment_payload` | `payment_required` (x402 v1/v2) | `payment_payload` (signed) | Sign a 402 challenge (EIP-3009; USDC preferred; Permit2 skipped) |
| `coinbase_status` | — | address, account_name, network | Wallet identity |
| `coinbase_balance` | network? | eth, usdc, network, address | On-chain balance |
| `faucet_eth` | network? (base-sepolia) | tx_hash, token, network, explorer | Testnet ETH (0.0001/claim, 1000/24h) |
| `faucet_usdc` | network? (base-sepolia) | tx_hash, token, network, explorer | Testnet USDC (1/claim, 10/24h) |

Auth: CDP API keys (server wallet). Transport: stdio JSON-RPC subprocess.

---

## 3. Local CDP server-wallet tools (`cdp_*`, default provider)

Native Hermes plugin tools (no subprocess) that manage a self-custodial CDP server wallet
in-process and sign x402 payments locally. Registered only when `x402.provider == "local"`.

| Tool | Inputs | Outputs | Usage |
|---|---|---|---|
| `cdp_wallet_status` | — | provider, address, account_name, network | Find the wallet address / confirm provisioning |
| `cdp_wallet_balance` | network? | eth, usdc, network, address | Check funds before paying/transferring |
| `cdp_faucet` | token (usdc/eth), network? (testnet) | tx_hash, token, network, address, explorer | Fund on testnet (no captcha) |
| `cdp_onramp` | purchase_currency?, network?, amount?, payment_currency?, country?, subdivision? | onramp_url, destination_address, network, purchase_currency | Buy crypto with fiat (mainnet funding) |
| `cdp_transfer` | to, amount, token?, network?, override? | tx_hash, to, amount, token, network, explorer | Send USDC/ETH out (guarded by per-call cap) |

x402 signing is delegated internally (`hermes_x402.cdp.signer.create_payment_payload`) by the
payment client — it is not a separate agent tool in local mode.

Auth: CDP API keys (`CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `CDP_WALLET_SECRET`).

---

## What a Hermes x402 agent needs from its wallet

Working backward from the agent's job — *discover a paid service, pay for it, keep going* —
the wallet must let the agent:

- **Know its identity** — its receive address + active network (to share for funding, or
  receive transfers).
- **Check funds** — per-asset balance, to decide whether it can afford a call and on which
  network/asset.
- **Sign an x402 payment** — turn a `PaymentRequired` (v1 or v2) into a signed
  `PaymentPayload`. This is the one non-negotiable primitive.
- **Fund itself on testnet** — a captcha-free faucet for dev/eval loops.
- **Fund itself on mainnet** — a fiat onramp (URL the human completes) so an unfunded agent
  can be topped up.
- **Send funds out** — direct transfer for refunds, paying a person, or consolidating.
- **Reconcile spend** — list what it paid (amount, tx, settlement status) for receipts and
  self-auditing.
- **Discover its own capabilities (stretch)** — which networks/assets/schemes (EIP-3009 vs
  Permit2) it can actually sign for, so it skips services it can't pay.
- **Acquire the right asset (stretch)** — convert/quote (e.g. ETH → USDC) when a service
  wants an asset the agent doesn't hold.

Everything else (orders, portfolios, market data from the brokerage MCP) is out of scope for
an x402 payments agent.

---

## 4. x402 Hermes Wallet Interface (our ask)

The canonical, provider-neutral contract we want every wallet provider to implement. Names
are logical; each provider exposes them under its prefix (`cdp_*` locally,
`mcp_coinbase_*` remotely).

| Tool | Inputs | Outputs | Usage |
|---|---|---|---|
| `wallet_status` | — | address, label/account_name, network, provider | Identity + active network; the agent's receive address |
| `wallet_balance` | network? (default configured), asset? | balances[] {symbol, amount, decimals}, usd_value? | Affordability check before a paid call; holdings report |
| `create_payment_payload` | `payment_required` (x402 v1 or v2) | `payment_payload` (signed; mirrors the selected requirement) | The core x402 step: sign a 402 so the call is retried with payment. EIP-3009; pick exact-EVM; skip Permit2 |
| `faucet` | token (usdc/eth), network (testnet only) | tx_hash, explorer | Captcha-free testnet funding for dev/eval |
| `onramp` | asset (USDC/ETH), network, amount?, currency?, country?, subdivision? | onramp_url | Mainnet funding: buy crypto with fiat via a single-use URL |
| `transfer` | to, amount, token, network? | tx_hash, explorer | Send funds out (refund, pay a person, consolidate). Must be policy-guarded (caps/approval) |
| `payments` | limit?, since? | entries[] {endpoint, amount_usdc, tx, settled, timestamp} | Reconcile/report spend; receipts for self-auditing |
| `supported` *(stretch)* | — | {networks[], assets[], schemes[]} | Capability discovery so the agent only attempts services it can pay (scheme/network match) |
| `convert` *(stretch)* | from_token, to_token, amount | quote or tx_hash | Acquire the asset a service requires before paying |

### Provider mapping

| Interface tool | Local (`cdp_*`) | Remote (`mcp_coinbase_*`) — proposed |
|---|---|---|
| `wallet_status` | `cdp_wallet_status` | `coinbase_status` |
| `wallet_balance` | `cdp_wallet_balance` | `coinbase_balance` |
| `create_payment_payload` | internal (`cdp.signer`) | `create_payment_payload` |
| `faucet` | `cdp_faucet` | `faucet_usdc` / `faucet_eth` |
| `onramp` | `cdp_onramp` | *(needs to be added)* |
| `transfer` | `cdp_transfer` | *(needs to be added)* |
| `payments` | `cdp_payments` (+ `hermes x402 payments` CLI) | *(needs to be added)* |
| `supported` *(stretch)* | *(not yet)* | *(not yet)* |
| `convert` *(stretch)* | *(not yet)* | *(not yet)* |

The internal Coinbase MCP (interface 1) today implements **none** of these x402-specific
primitives — `create_payment_payload`, the wallet `faucet`/`onramp`, and the x402-flavored
`wallet_status`/`wallet_balance` are the additions we are asking the Coinbase MCP team to
build so the `coinbase_mcp` provider can match this interface.
