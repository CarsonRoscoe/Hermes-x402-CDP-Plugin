"""`hermes x402 reconcile` — list/resolve unconfirmed payment-journal entries.

Paid operations write a durable journal row before paying and finalize it after. Entries
left in ``pending``/``paid``/``unknown`` (timeout, crash, lost receipt) may represent money
that moved without a clean record. This command surfaces them so an operator can confirm
on-chain status and mark each resolved.
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
    if rows:
        print("\nResolve after checking on-chain: hermes x402 reconcile --resolve <id> --state paid|failed")
    return 0
