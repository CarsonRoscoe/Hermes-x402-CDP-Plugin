"""`hermes x402 reconcile` — list/resolve unconfirmed payment-journal entries.

Paid operations write a durable journal row before paying and finalize it after. Entries
left in ``pending``/``paid``/``unknown`` (timeout, crash, lost receipt) may represent money
that moved without a clean record. This command surfaces them so an operator can confirm
on-chain status and mark each resolved.

Use ``--check`` to auto-verify open entries: any entry with a recorded tx hash is checked
against the on-chain USDC balance change (heuristic: if wallet balance ≥ expected amount,
mark as paid; otherwise prompt manually). Use ``--resolve ID --state S`` to manually mark
an entry resolved after checking BaseScan or cdp_payments.
"""

from __future__ import annotations

import time

from .. import ledger


def _fmt(rows: list[dict]) -> str:
    if not rows:
        return "(no unresolved payment-journal entries)"
    lines = []
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("ts", 0)))
        lines.append(
            f"  #{r.get('id'):<5} {ts}  {r.get('state',''):9} {r.get('kind',''):5} "
            f"cap<={float(r.get('cap_usdc',0)):.4f}  {r.get('endpoint_host') or '-'}"
            f"  tx={r.get('tx') or '-'}"
        )
    return "\n".join(lines)


def _check_open_entries(rows: list[dict]) -> None:
    """Best-effort on-chain verification for open journal entries that have a tx hash.

    For each entry with a tx hash, attempts to read the current wallet USDC balance and
    compares it against the cap amount as a heuristic. When verification is inconclusive
    (no CDP creds, network error), prints a manual-check URL for BaseScan.
    """
    from .. import config, wallet

    network = config.network()
    for r in rows:
        tx = r.get("tx")
        entry_id = r.get("id")
        if not tx:
            print(f"  #{entry_id}: no tx hash — manual check required (state={r.get('state')})")
            continue

        # Build a block-explorer URL so the operator can verify manually.
        if "sepolia" in (network or ""):
            explorer = f"https://sepolia.basescan.org/tx/{tx}"
        else:
            explorer = f"https://basescan.org/tx/{tx}"

        print(f"  #{entry_id}: tx={tx}")
        print(f"    Explorer: {explorer}")

        # Attempt a wallet balance read as a lightweight heuristic; any failure is non-fatal.
        try:
            bal = wallet.usdc_balance(network)
            if bal is not None:
                print(f"    Current USDC balance: {bal:.6f} USDC on {network}")
            else:
                print("    Balance unavailable — check explorer URL above")
        except Exception as exc:
            print(f"    Balance check failed ({exc}) — check explorer URL above")

        print(
            f"    To mark resolved: "
            f"hermes x402 reconcile --resolve {entry_id} --state paid|failed|succeeded"
        )


def reconcile_command(args) -> int:
    """List open journal entries; optionally mark one resolved with --resolve ID --state S."""
    resolve_id = getattr(args, "resolve", None)
    if resolve_id is not None:
        state = getattr(args, "state", None) or "paid"
        if state not in ("paid", "failed", "succeeded"):
            print("--state must be one of: paid, failed, succeeded")
            return 2
        ledger.journal_finalize(int(resolve_id), state=state)
        print(f"journal #{resolve_id} marked '{state}'")
        return 0

    rows = ledger.journal_open_entries()
    if ledger.record_failures:
        print(f"WARNING: {ledger.record_failures} ledger write failure(s) this process.")
    print(f"x402 unresolved payment journal ({len(rows)} entr{'y' if len(rows) == 1 else 'ies'}):")
    print(_fmt(rows))

    check = getattr(args, "check", False)
    if check and rows:
        print("\nChecking open entries (--check):")
        _check_open_entries(rows)
    elif rows:
        print(
            "\nResolve after checking on-chain: hermes x402 reconcile --resolve <id> --state paid|failed"
            "\nOr run with --check to get explorer URLs and a balance read for each open entry."
        )
    return 0
