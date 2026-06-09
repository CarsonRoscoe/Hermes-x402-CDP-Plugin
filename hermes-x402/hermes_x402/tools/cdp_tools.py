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
    """Handler for ``cdp_transfer`` — moves real funds; guarded by the per-call cap."""
    from .. import config
    from ..cdp import wallet_ops

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

    # Guard: USDC transfers are capped by the per-call cap unless explicitly overridden.
    cap = config.max_price_usdc()
    if token == "usdc" and not override and cap > 0:
        try:
            if float(amount) > cap:
                return _err(
                    f"transfer of {amount} USDC exceeds the per-call cap of {cap} USDC; "
                    "raise x402.max_price_usdc or pass override=true",
                    amount=amount,
                    cap_usdc=cap,
                )
        except (TypeError, ValueError):
            return _err(f"invalid amount {amount!r}")

    try:
        return json.dumps(wallet_ops.transfer(to, amount, token, network))
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
    return json.dumps({"payments": payments, "count": len(payments), "total_usdc": total})
