"""Plugin hooks beyond budget/spend lifecycle.

This module currently provides a transform hook that nudges the model toward the
correct paid-MCP retry flow when a native ``mcp_*`` call reports payment-required.
"""

from __future__ import annotations

import json
from typing import Any


def _is_payment_required_payload(parsed: Any, raw_text: str) -> bool:
    if isinstance(parsed, dict):
        if parsed.get("x402Version") or parsed.get("accepts"):
            return True
        err = str(parsed.get("error", "")).lower()
        detail = str(parsed.get("detail", "")).lower()
        if "payment required" in err or "payment-required" in err:
            return True
        if "payment required" in detail or "payment-required" in detail:
            return True
    text = raw_text.lower()
    return (
        ("payment required" in text or "payment-required" in text or '"status": 402' in text)
        and ("x402" in text or "accepts" in text)
    )


def _hint_for_retry(tool_name: str, args: dict | None) -> str:
    args_json = json.dumps(args or {}, separators=(", ", ": "))
    return (
        "\n\nx402 retry hint:\n"
        "This native MCP call appears to require payment.\n"
        "Call x402_retry_mcp_payment with the SAME tool and arguments:\n"
        f'{{"tool_name": "{tool_name}", "arguments": {args_json}}}\n'
        "If the retry returns unknown_settlement, check cdp_payments or run "
        "`hermes x402 reconcile` before trying again."
    )


def _bazaar_search_tool_prefix() -> str:
    """Return the mcp_* name prefix for the configured Bazaar search_resources tool.

    Resolves the actual server name from mcp_servers config so the guard works
    regardless of what the operator named their Bazaar server (e.g. "bazaar",
    "cdp-bazaar", etc.).  Falls back to the conventional default "mcp_bazaar_".
    """
    try:
        from .mcp_servers import BAZAAR_SERVER_NAME
        from .tools.retry_mcp import _load_mcp_servers, _sanitize

        servers = _load_mcp_servers()
        # Find the Bazaar server name in mcp_servers (same logic as _find_bazaar_proxy_tool).
        for name, scfg in servers.items():
            url = scfg.get("url", "") if isinstance(scfg, dict) else ""
            if "x402/discovery/mcp" in url or name == BAZAAR_SERVER_NAME:
                return f"mcp_{_sanitize(name)}_search_resources"
    except Exception:
        pass
    return "mcp_bazaar_search_resources"


def on_transform_tool_result(
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    **_: Any,
) -> str | None:
    """Append paid-MCP recovery guidance to payment-required native mcp_* results."""
    if not isinstance(result, str):
        return None
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp_"):
        return None
    # Skip Bazaar search_resources results — those return discovered resource lists,
    # not payment requirements. Resolve the prefix dynamically to handle any server name.
    if tool_name.startswith(_bazaar_search_tool_prefix()):
        return None

    parsed: Any = None
    try:
        parsed = json.loads(result)
    except (ValueError, TypeError):
        parsed = None

    if not _is_payment_required_payload(parsed, result):
        return None
    if "x402_retry_mcp_payment" in result:
        return None
    return result + _hint_for_retry(tool_name, args if isinstance(args, dict) else None)
