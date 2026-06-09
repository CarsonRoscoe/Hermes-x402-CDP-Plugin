"""`hermes x402 spend | payments` — read the spend ledger."""

from __future__ import annotations

import time

from .. import ledger


def _fmt(rows: list[dict]) -> str:
    if not rows:
        return "(no x402 payments recorded yet)"
    lines = []
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("ts", 0)))
        lines.append(
            f"  {ts}  {r.get('kind',''):9}  {float(r.get('amount_usdc',0)):.6f} USDC  "
            f"{r.get('endpoint_host') or '-'}"
        )
    return "\n".join(lines)


def spend_command(args) -> int:
    """Total + recent spend summary."""
    all_time = ledger.all_time_total()
    rows = ledger.recent_spend(50)
    window_total = sum(float(r.get("amount_usdc", 0)) for r in rows)
    print(f"x402 all-time total: {all_time:.6f} USDC")
    if len(rows) == 50:
        print(f"x402 recent 50 payments: {window_total:.6f} USDC (may not equal all-time total)")
    print(_fmt(rows[:20]))
    return 0


def payments_command(args) -> int:
    """List recent individual payments."""
    print(_fmt(ledger.recent_spend(50)))
    return 0
