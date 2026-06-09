"""Native ``cdp_*`` agent tools for the local (self-custodial) CDP wallet provider.

These are registered only when ``x402.provider == "local"`` (gated by ``check_fn``). Each
handler takes ``(args: dict, **kwargs)`` and returns a JSON string. They wrap the CDP core
in :mod:`hermes_x402.cdp.wallet_ops`; CDP credential / network errors are returned as a
structured ``{"error": ...}`` rather than raised, so a misconfigured wallet never crashes
the agent loop.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _err(message: str, **extra) -> str:
    out = {"error": message}
    out.update(extra)
    return json.dumps(out)


def cdp_wallet_status(args: dict, **kwargs) -> str:
    """Handler for ``cdp_wallet_status``."""
    from ..cdp import wallet_ops

    try:
        return json.dumps(wallet_ops.status())
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}", hint="Check CDP credentials in ~/.hermes/.env")


def cdp_wallet_balance(args: dict, **kwargs) -> str:
    """Handler for ``cdp_wallet_balance``."""
    from .. import config
    from ..cdp import wallet_ops

    args = args or {}
    network = args.get("network") or config.network()
    asset = args.get("asset")
    try:
        return json.dumps(wallet_ops.balances(network, asset))
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}", network=network)


def cdp_faucet(args: dict, **kwargs) -> str:
    """Handler for ``cdp_faucet`` (testnet only)."""
    from .. import config
    from ..cdp import wallet_ops

    args = args or {}
    token = (args.get("token") or "usdc").lower()
    network = args.get("network") or config.network()
    try:
        return json.dumps(wallet_ops.faucet(token, network))
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}", token=token, network=network)


def cdp_onramp(args: dict, **kwargs) -> str:
    """Handler for ``cdp_onramp`` (mainnet fiat purchase URL)."""
    from .. import config
    from ..cdp import wallet_ops

    args = args or {}
    try:
        result = wallet_ops.onramp_url(
            purchase_currency=str(args.get("asset") or "USDC"),
            network=args.get("network") or config.network(),
            amount=args.get("amount"),
            payment_currency=str(args.get("currency") or "USD"),
            country=args.get("country"),
            subdivision=args.get("subdivision"),
        )
        return json.dumps(result)
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}")


def cdp_transfer(args: dict, **kwargs) -> str:
    """Handler for ``cdp_transfer`` — moves real funds; guarded by per-call and per-session caps."""
    from .. import config, ledger
    from ..cdp import wallet_ops
    from ..session import current_session_id

    args = args or {}
    to = args.get("to")
    amount = args.get("amount")
    token = (args.get("token") or "usdc").lower()
    network = args.get("network") or config.network()
    override = bool(args.get("override"))

    if not to:
        return _err("'to' (recipient address) is required")
    if amount is None:
        return _err("'amount' is required")

    try:
        amount_float = float(amount)
    except (TypeError, ValueError):
        return _err(f"invalid amount {amount!r}")

    # Guard 1: USDC per-call cap (override=true bypasses this).
    cap = config.max_price_usdc()
    if token == "usdc" and not override and cap > 0:
        if amount_float > cap:
            return _err(
                f"transfer of {amount} USDC exceeds the per-call cap of {cap} USDC; "
                "raise x402.max_price_usdc or pass override=true",
                amount=amount,
                cap_usdc=cap,
            )

    # Guard 2: per-session cumulative transfer ceiling (applies even with override=true).
    # This prevents unbounded fund drainage across multiple calls in one session.
    session_id = current_session_id(kwargs)
    if token == "usdc":
        transfer_budget = config.session_transfer_budget_usdc()
        if transfer_budget > 0 and session_id:
            try:
                already_transferred = ledger.session_transfer_total(session_id)
                if already_transferred + amount_float > transfer_budget:
                    return _err(
                        f"cumulative transfer of {already_transferred + amount_float:.4f} USDC "
                        f"would exceed the per-session transfer budget of {transfer_budget:.4f} USDC "
                        f"(already transferred {already_transferred:.4f} USDC this session); "
                        "raise x402.session_transfer_budget_usdc in config to continue.",
                        amount=amount,
                        already_transferred_usdc=already_transferred,
                        session_transfer_budget_usdc=transfer_budget,
                    )
            except Exception as exc:
                logger.warning("hermes-x402: session transfer budget check failed: %s", exc)
                if config.is_strict():
                    return _err(
                        "session transfer budget could not be verified and failure_mode is strict; "
                        "refusing the transfer.",
                    )

    try:
        result = wallet_ops.transfer(to, amount, token, network)
        # Record the transfer so the session ceiling accounts for it.
        if token == "usdc" and session_id:
            ledger.record_payment(
                kind="transfer",
                amount_usdc=amount_float,
                network=network,
                endpoint=to,
                transaction=result.get("tx_hash"),
                session_id=session_id,
            )
        return json.dumps(result)
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}", to=to, amount=amount, token=token, network=network)


def cdp_payments(args: dict, **kwargs) -> str:
    """Handler for ``cdp_payments`` — recent x402 payment receipts from the local ledger."""
    from .. import ledger

    args = args or {}
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 200))

    rows = ledger.recent_spend(limit)

    since = args.get("since")
    if since is not None:
        try:
            since_ts = float(since)
            rows = [r for r in rows if float(r.get("ts", 0)) >= since_ts]
        except (TypeError, ValueError):
            pass

    payments = [
        {
            "timestamp": r.get("ts"),
            "endpoint": r.get("endpoint_host"),
            "amount_usdc": float(r.get("amount_usdc", 0)),
            "tx": r.get("tx"),
            "kind": r.get("kind"),
            "network": r.get("network"),
            # Rows are written only after settlement is confirmed.
            "settled": True,
        }
        for r in rows
    ]
    total = round(sum(p["amount_usdc"] for p in payments), 6)

    # Surface any open/unresolved journal entries so the caller knows about in-flight
    # or lost payments not yet in the settled ledger.
    pending_count = len(ledger.journal_open_entries(limit=100))
    result: dict = {
        "payments": payments,
        "count": len(payments),
        "total_usdc": total,
        "pending_journal_entries": pending_count,
    }
    if pending_count:
        result["pending_note"] = (
            f"{pending_count} payment(s) are in an unresolved state (pending/unknown). "
            "Run `hermes x402 reconcile` to inspect and resolve them."
        )
    return json.dumps(result)
