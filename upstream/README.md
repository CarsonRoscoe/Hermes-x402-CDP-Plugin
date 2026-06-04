# Upstream deliverable (for the Hermes Agent PR)

These are the *thin* artifacts intended to land in
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent). They are kept
here (not applied to a vendored Hermes checkout) so they can be dropped into a clean PR.
The heavy logic stays in the `hermes-x402` companion package; this is just the glue that
adds a `hermes setup --coinbase` flag delegating to the companion's `run_x402_onboarding`.

## Contents

- `setup_coinbase_flag.md` — the exact, minimal change to add `--coinbase` to
  `hermes_cli/main.py` + `hermes_cli/setup.py` (mirrors the existing `--portal` path).
- `docs/paying-with-x402.md` — a short docs page.

> Note: there is no `plugins/model-providers/x402/` stub. Paying for **inference** via
> `provider: x402` is out of scope for now (needs `upto` / `batch-settlement` schemes we
> are not implementing yet). When that lands, a provider stub can be added here.

## Why thin

A 170k-star repo merges small, reviewable PRs. Everything substantial is `pip install
hermes-x402`; this PR is one flag + a docs page.
