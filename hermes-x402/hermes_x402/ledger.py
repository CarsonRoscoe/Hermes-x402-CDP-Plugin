"""Spend / payment ledger (sqlite).

Records every x402 payment (HTTP, MCP, inference) so the agent and user can audit spend
(``hermes x402 spend|payments``, the ``/x402`` summary, and the session-end hook read
from here). Stored at ``config.ledger_path()`` so it is profile-aware.

Only non-sensitive fields are persisted: amount, network, endpoint *host* (never full
URLs with query params), kind, optional tx hash, timestamp, session id. No private keys,
signatures, headers, or request/response bodies.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlparse

from .config import ensure_data_dir, ledger_path

logger = logging.getLogger(__name__)

#: Count of ledger write failures this process. A non-zero value means money may have moved
#: without a durable local record — operators should reconcile. Exposed for status/metrics.
record_failures = 0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL,
    amount_usdc REAL NOT NULL,
    network TEXT,
    endpoint_host TEXT,
    tx TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_session ON payments(session_id);

CREATE TABLE IF NOT EXISTS payment_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    updated_ts REAL,
    fingerprint TEXT,
    idempotency_key TEXT,
    state TEXT NOT NULL,
    kind TEXT,
    endpoint_host TEXT,
    cap_usdc REAL,
    amount_usdc REAL,
    tx TEXT,
    session_id TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_fp ON payment_journal(fingerprint);
CREATE INDEX IF NOT EXISTS idx_journal_state ON payment_journal(state);
"""

#: Journal states. A paid call moves pending -> succeeded on a clean result, -> unknown on
#: timeout/crash (money may have moved), or -> failed when it errored before any payment.
#: ``paid`` is used by reconciliation when settlement is confirmed but the result was lost.
JOURNAL_OPEN_STATES = ("pending", "paid", "unknown")


class JournalError(Exception):
    """Raised when the durable payment journal cannot be written (so callers can fail closed)."""


class BudgetExceededError(Exception):
    """Raised when an atomic reservation would push session spend past the budget."""

    def __init__(self, spent: float, reserved: float, cap: float, budget: float) -> None:
        super().__init__(
            f"spent {spent:.4f} + reserved {reserved:.4f} + call {cap:.4f} exceeds "
            f"budget {budget:.4f} USDC"
        )
        self.spent, self.reserved, self.cap, self.budget = spent, reserved, cap, budget


@contextmanager
def _connect():
    ensure_data_dir()
    conn = sqlite3.connect(str(ledger_path()))
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _host(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    try:
        parsed = urlparse(endpoint)
        return parsed.netloc or endpoint.split("/")[0]
    except Exception:
        return None


def record_payment(
    *,
    kind: str,
    amount_usdc: float,
    network: str | None = None,
    endpoint: str | None = None,
    transaction: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Append a payment row. ``kind`` is one of "http" | "mcp" | "inference"."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO payments (ts, session_id, kind, amount_usdc, network, endpoint_host, tx)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    session_id,
                    kind,
                    float(amount_usdc or 0),
                    network,
                    _host(endpoint),
                    transaction,
                ),
            )
        return True
    except Exception as exc:
        global record_failures
        record_failures += 1
        # A failed write means a (possibly settled) payment has no durable record. Make it
        # loud so operators can reconcile; do not include URLs/PII (only kind + amount).
        logger.warning(
            "hermes-x402: FAILED to record %s payment of %.6f USDC (count=%d): %s",
            kind, float(amount_usdc or 0), record_failures, exc,
        )
        return False


def recent_spend(limit: int = 20) -> list[dict]:
    """Return the most recent payment rows (newest first)."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ts, session_id, kind, amount_usdc, network, endpoint_host, tx"
                " FROM payments ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("hermes-x402: failed to read spend: %s", exc)
        return []


def session_total(session_id: str | None) -> float:
    """Return total USDC spent in a session (0.0 if none / unknown)."""
    if not session_id:
        return 0.0
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_usdc), 0) FROM payments WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return float(row[0] or 0)
    except Exception as exc:
        logger.debug("hermes-x402: failed to total session spend: %s", exc)
        return 0.0


def session_transfer_total(session_id: str | None) -> float:
    """Return total USDC directly transferred (kind='transfer') in a session."""
    if not session_id:
        return 0.0
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_usdc), 0) FROM payments"
                " WHERE session_id = ? AND kind = 'transfer'",
                (session_id,),
            ).fetchone()
            return float(row[0] or 0)
    except Exception as exc:
        logger.debug("hermes-x402: failed to total session transfer spend: %s", exc)
        return 0.0


def all_time_total() -> float:
    """Return the all-time total USDC recorded across all sessions."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_usdc), 0) FROM payments"
            ).fetchone()
            return float(row[0] or 0)
    except Exception as exc:
        logger.debug("hermes-x402: failed to compute all-time total: %s", exc)
        return 0.0


def on_session_end(session_id=None, completed=None, interrupted=None, **kwargs) -> None:
    """``on_session_end`` hook: log a one-line spend summary. Never raises."""
    try:
        total = session_total(session_id)
        if total > 0:
            logger.info("hermes-x402: session spend total %.6f USDC", total)
    except Exception as exc:
        logger.debug("hermes-x402: on_session_end summary failed: %s", exc)


# --------------------------------------------------------------------------- #
# Durable payment journal (write-ahead before paying; finalize after).
# --------------------------------------------------------------------------- #
def journal_begin(
    *,
    fingerprint: str,
    idempotency_key: str | None,
    kind: str,
    endpoint: str | None,
    cap_usdc: float | None,
    session_id: str | None,
    budget_usdc: float = 0.0,
) -> int:
    """Atomically reserve budget and write a ``pending`` journal row *before* a paid call.

    Under a single ``BEGIN IMMEDIATE`` transaction we sum already-settled spend plus the caps
    of still-open journal entries for the session; if ``spent + reserved + this call's cap``
    would exceed ``budget_usdc`` we abort (no row written) so two concurrent calls cannot each
    pass an independent budget check and both overspend (R5). Returns the new row id.

    Raises ``BudgetExceededError`` when the reservation doesn't fit, or ``JournalError`` if the
    journal cannot be written (so a strict caller can refuse to pay an untracked operation).
    """
    cap = float(cap_usdc or 0)
    try:
        ensure_data_dir()
        # isolation_level=None: we manage transactions explicitly for the reservation lock.
        conn = sqlite3.connect(str(ledger_path()), isolation_level=None)
        try:
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            try:
                if budget_usdc and budget_usdc > 0 and session_id:
                    spent = conn.execute(
                        "SELECT COALESCE(SUM(amount_usdc), 0) FROM payments WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()[0] or 0
                    placeholders = ",".join("?" for _ in JOURNAL_OPEN_STATES)
                    reserved = conn.execute(
                        f"SELECT COALESCE(SUM(cap_usdc), 0) FROM payment_journal"
                        f" WHERE session_id = ? AND state IN ({placeholders})",
                        (session_id, *JOURNAL_OPEN_STATES),
                    ).fetchone()[0] or 0
                    if float(spent) + float(reserved) + cap > budget_usdc:
                        conn.execute("ROLLBACK")
                        raise BudgetExceededError(float(spent), float(reserved), cap, budget_usdc)
                now = time.time()
                cur = conn.execute(
                    "INSERT INTO payment_journal"
                    " (ts, updated_ts, fingerprint, idempotency_key, state, kind, endpoint_host,"
                    "  cap_usdc, amount_usdc, tx, session_id, result_json)"
                    " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, NULL, NULL, ?, NULL)",
                    (now, now, fingerprint, idempotency_key, kind, _host(endpoint), cap, session_id),
                )
                conn.execute("COMMIT")
                return int(cur.lastrowid)
            except BudgetExceededError:
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
    except BudgetExceededError:
        raise
    except Exception as exc:
        global record_failures
        record_failures += 1
        logger.warning("hermes-x402: FAILED to open payment journal entry: %s", exc)
        raise JournalError(str(exc)) from exc


def journal_begin_transfer(
    *,
    fingerprint: str,
    idempotency_key: str | None,
    endpoint: str | None,
    cap_amount: float,
    session_id: str,
    budget: float,
    kind: str = "transfer",
) -> int:
    """Atomically reserve per-session transfer budget before moving funds.

    Uses only journal rows for accounting so transfer budget enforcement remains correct even
    when the settled ``payments`` write fails after a successful transfer. ``amount_usdc`` is
    treated as a generic numeric amount for transfer kinds (USDC uses USDC units; ETH uses ETH
    units in ``transfer_eth`` rows).
    """
    cap = float(cap_amount or 0)
    if cap <= 0:
        raise JournalError("transfer cap_amount must be > 0")
    if not session_id:
        raise JournalError("session_id is required for transfer reservation")
    if budget <= 0:
        raise JournalError("transfer budget must be > 0")
    try:
        ensure_data_dir()
        conn = sqlite3.connect(str(ledger_path()), isolation_level=None)
        try:
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            try:
                spent = conn.execute(
                    "SELECT COALESCE(SUM(amount_usdc), 0) FROM payment_journal"
                    " WHERE session_id = ? AND kind = ? AND state = 'succeeded'",
                    (session_id, kind),
                ).fetchone()[0] or 0
                placeholders = ",".join("?" for _ in JOURNAL_OPEN_STATES)
                reserved = conn.execute(
                    f"SELECT COALESCE(SUM(cap_usdc), 0) FROM payment_journal"
                    f" WHERE session_id = ? AND kind = ? AND state IN ({placeholders})",
                    (session_id, kind, *JOURNAL_OPEN_STATES),
                ).fetchone()[0] or 0
                if float(spent) + float(reserved) + cap > budget:
                    conn.execute("ROLLBACK")
                    raise BudgetExceededError(float(spent), float(reserved), cap, budget)
                now = time.time()
                cur = conn.execute(
                    "INSERT INTO payment_journal"
                    " (ts, updated_ts, fingerprint, idempotency_key, state, kind, endpoint_host,"
                    "  cap_usdc, amount_usdc, tx, session_id, result_json)"
                    " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, NULL, NULL, ?, NULL)",
                    (now, now, fingerprint, idempotency_key, kind, _host(endpoint), cap, session_id),
                )
                conn.execute("COMMIT")
                return int(cur.lastrowid)
            except BudgetExceededError:
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
    except BudgetExceededError:
        raise
    except Exception as exc:
        global record_failures
        record_failures += 1
        logger.warning("hermes-x402: FAILED to reserve transfer budget: %s", exc)
        raise JournalError(str(exc)) from exc


def journal_finalize(
    journal_id: int,
    *,
    state: str,
    amount_usdc: float | None = None,
    tx: str | None = None,
    result_json: str | None = None,
) -> None:
    """Update a journal row to a terminal/known state. Best-effort but logged loudly."""
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE payment_journal SET state = ?, updated_ts = ?, amount_usdc = ?,"
                " tx = ?, result_json = ? WHERE id = ?",
                (state, time.time(), float(amount_usdc or 0), tx, result_json, journal_id),
            )
    except Exception as exc:
        global record_failures
        record_failures += 1
        logger.warning(
            "hermes-x402: FAILED to finalize payment journal %s -> %s: %s", journal_id, state, exc
        )


def journal_lookup(fingerprint: str) -> dict | None:
    """Return the most recent journal row for a fingerprint (newest first), or None."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM payment_journal WHERE fingerprint = ? ORDER BY id DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.debug("hermes-x402: journal lookup failed: %s", exc)
        return None


def journal_open_entries(limit: int = 100) -> list[dict]:
    """Return journal rows in non-terminal states (pending/paid/unknown) for reconciliation."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in JOURNAL_OPEN_STATES)
            rows = conn.execute(
                f"SELECT * FROM payment_journal WHERE state IN ({placeholders})"
                " ORDER BY id DESC LIMIT ?",
                (*JOURNAL_OPEN_STATES, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("hermes-x402: journal open-entries read failed: %s", exc)
        return []
