"""Budget enforcement via the ``pre_tool_call`` hook.

Blocks the paid tools (``x402_request``, ``x402_retry_mcp_payment``) when the session's
cumulative spend has already reached ``x402.session_budget_usdc``. Per-call price caps
(``x402.max_price_usdc`` / the tool's ``max_price_usdc`` arg) are enforced inside the
tools via ``tools._paid.effective_cap`` + the payment client's cap check; this hook adds
the session ceiling.

``pre_tool_call`` may return ``{"action": "block", "message": ...}`` to stop a call.
"""

from __future__ import annotations

import logging

from . import config, ledger

logger = logging.getLogger(__name__)

# Only the x402 payment tools are session-budget-gated. ``cdp_transfer`` is intentionally
# excluded: it is a direct wallet operation (not an x402 payment) and has its own per-call
# cap check in the tool handler. Adding it here would conflate wallet transfers with x402
# spend and break the accounting. Keep this set minimal and explicit.
_PAID_TOOLS = {"x402_request", "x402_retry_mcp_payment"}


def pre_tool_call(tool_name=None, args=None, task_id=None, **kwargs):
    """Block paid tools when the session budget is (or would be) exhausted.

    When the pending call has a known per-call cap, we block if completing it could push
    cumulative spend past the budget (``spent + cap > budget``) so a single call can't
    overshoot the ceiling. Uncapped calls fall back to ``spent >= budget``.
    """
    if tool_name not in _PAID_TOOLS:
        return None
    try:
        budget = config.session_budget_usdc()
        if budget <= 0:
            return None
        spent = ledger.session_total(task_id)

        from .tools._paid import effective_cap  # local import avoids an import cycle

        cap = effective_cap((args or {}).get("max_price_usdc"))
        if spent >= budget or (cap > 0 and spent + cap > budget):
            projected = f" a call up to {cap:.4f} USDC would exceed it;" if cap > 0 else ""
            return {
                "action": "block",
                "message": (
                    f"x402 session budget reached: spent {spent:.4f} of {budget:.4f} USDC;"
                    f"{projected} raise x402.session_budget_usdc in config to continue."
                ),
            }
    except Exception as exc:
        # Fail closed under strict mode: if we can't verify the budget, don't let money move.
        if config.is_strict():
            logger.warning("hermes-x402: budget check failed; blocking paid call (strict): %s", exc)
            return {
                "action": "block",
                "message": (
                    "x402 budget could not be verified and failure_mode is strict; refusing "
                    "the paid call. Set x402.failure_mode: best-effort to allow."
                ),
            }
        logger.warning("hermes-x402: budget check failed; allowing call (best-effort): %s", exc)
    return None
