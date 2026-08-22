"""x402_retry_mcp_payment — pay for a native MCP tool that returned payment-required.

The reactive model: the agent calls any native ``mcp_*`` tool (e.g. a paid Bazaar
``proxy_tool_call`` or any connected paid MCP server). If it returns payment-required, the
agent calls this tool with the SAME ``tool_name`` and ``arguments``. We resolve the
upstream server URL from Hermes's ``mcp_servers`` config, recover the real upstream tool
name, then re-issue the call through the x402 SDK's ``x402MCPClient`` using the shared
payment client (local CDP signer today; payment injected into ``_meta``).

URL-based MCP servers only (stdio upstreams would need spawning a second instance; deferred).
"""

from __future__ import annotations

import json
import logging
import re

from .. import config
from .._async import UnknownSettlementError, run_async
from ..coinbase_mcp.payment_client import min_usdc_in_accepts
from ..session import current_session_id
from ._paid import (
    StructuredToolError,
    effective_cap,
    extract_server_error,
    record_paid_call,
    run_journaled,
)

logger = logging.getLogger(__name__)

try:
    from x402.schemas import NoMatchingRequirementsError as _NoMatchingRequirementsError
except Exception:  # pragma: no cover - dependency shape varies across SDK versions
    _NoMatchingRequirementsError = None


try:
    # Prefer Hermes's own sanitizer so the generated ``mcp_*`` names stay in lockstep with
    # the host (single source of truth). Hermes exposes its modules as top-level ``tools.*``.
    from tools.mcp_tool import sanitize_mcp_name_component as _sanitize
except Exception:
    def _sanitize(value: str) -> str:
        """Fallback mirror of Hermes's ``sanitize_mcp_name_component`` (standalone/tests).

        Hermes builds agent-facing names as ``mcp_{server}_{tool}`` where both components are
        sanitized by replacing every character outside ``[A-Za-z0-9_]`` with ``_`` (case
        preserved, underscores kept).
        """
        return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def _is_no_matching_requirements_error(exc: Exception) -> bool:
    """SDK-compatible detection for Permit2-only/no-compatible-scheme failures."""
    if _NoMatchingRequirementsError is not None and isinstance(exc, _NoMatchingRequirementsError):
        return True
    inner = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if _NoMatchingRequirementsError is not None and inner is not None:
        if isinstance(inner, _NoMatchingRequirementsError):
            return True
    name = type(exc).__name__
    msg = str(exc)
    return "NoMatchingRequirements" in name or "NoMatchingRequirements" in msg


def _load_mcp_servers() -> dict:
    try:
        from hermes_cli.config import load_config

        return (load_config() or {}).get("mcp_servers") or {}
    except Exception as exc:
        logger.debug("hermes-x402: could not read mcp_servers config: %s", exc)
        return {}


def resolve_server(tool_name: str, servers: dict | None = None) -> tuple[str, str, dict]:
    """Resolve ``mcp_{server}_{tool}`` -> (server_name, sanitized_tool_suffix, server_cfg).

    Longest sanitized-prefix match against ``mcp_servers`` keys, so server names containing
    underscores are handled. The suffix is the *sanitized* tool name; the real upstream name
    is recovered later via ``resolve_upstream_name`` (Hermes calls handlers with the original
    tool name, which can differ, e.g. ``get-sum`` -> ``get_sum``). Raises ``KeyError`` if no
    server matches.

    Accepts an optional pre-loaded ``servers`` dict to avoid repeated config reads when the
    caller already has it.
    """
    rest = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
    if servers is None:
        servers = _load_mcp_servers()
    best: tuple[str, str, dict] | None = None
    for name, scfg in servers.items():
        safe = _sanitize(name)
        if rest == safe or rest.startswith(safe + "_"):
            if best is None or len(safe) > len(_sanitize(best[0])):
                suffix = rest[len(safe) + 1 :] if rest != safe else ""
                best = (name, suffix, scfg if isinstance(scfg, dict) else {})
    if best is None:
        raise KeyError(f"no mcp_servers entry matches tool {tool_name!r}")
    return best


def resolve_upstream_name(tools, sanitized_suffix: str) -> str | None:
    """Map a sanitized tool suffix back to the server's real tool name via its tool list."""
    for t in tools:
        name = t.get("name") if isinstance(t, dict) else getattr(t, "name", "")
        if _sanitize(name) == sanitized_suffix:
            return name
    return None


# Our own plugin tools — never Bazaar-discovered resource names.
_OWN_TOOLS = {"x402_request", "x402_retry_mcp_payment"}


def _looks_like_bazaar_resource(tool_name: str) -> bool:
    """True for a Bazaar-*discovered* resource name (e.g. ``x402_get_https___...``).

    These come back from ``search_resources`` and are reachable only via the proxy — they are
    never registered as callable ``mcp_*`` tools, so passing one as ``tool_name`` is the most
    common retry mistake (see ``_bazaar_redirect``).
    """
    return tool_name.startswith("x402_") and tool_name not in _OWN_TOOLS


def _find_bazaar_proxy_tool(servers: dict) -> str | None:
    """Return the agent-facing ``mcp_{server}_proxy_tool_call`` for the configured Bazaar server.

    Identifies the Bazaar by its discovery URL (or the conventional name) so the redirect points
    at whatever the server is actually called in ``mcp_servers``. Accepts a pre-loaded servers
    dict to avoid a redundant config read when called from ``_bazaar_redirect``.
    """
    for name, scfg in servers.items():
        url = scfg.get("url", "") if isinstance(scfg, dict) else ""
        if "x402/discovery/mcp" in url or name == "bazaar":
            return f"mcp_{_sanitize(name)}_proxy_tool_call"
    return None


def _bazaar_redirect(tool_name: str, arguments: dict, servers: dict) -> dict:
    """Build a ready-to-use ``x402_retry_mcp_payment`` call that fixes a wrong Bazaar tool_name.

    The caller passed a discovered resource name; the correct retry targets the proxy tool with
    ``arguments={toolName, parameters}``. If ``arguments`` is already proxy-shaped we pass it
    through (only the tool_name was wrong); otherwise we wrap it. Accepts a pre-loaded ``servers``
    dict so the caller's config read is reused rather than repeated.
    """
    proxy = _find_bazaar_proxy_tool(servers) or "mcp_bazaar_proxy_tool_call"
    if isinstance(arguments, dict) and "toolName" in arguments:
        fix_args = arguments
    else:
        fix_args = {"toolName": tool_name, "parameters": arguments or {}}
    return {
        "error": "wrong_tool_name_for_retry",
        "detail": (
            f"{tool_name!r} is a Bazaar-discovered resource, not a callable MCP tool. Bazaar "
            "resources are reachable only through the proxy tool, so retry the proxy — not the "
            "resource name."
        ),
        "fix": {"tool_name": proxy, "arguments": fix_args},
        "hint": (
            f"Re-call x402_retry_mcp_payment with tool_name={proxy!r} and arguments set to the "
            "values under 'fix' (the same toolName/parameters you gave the proxy call)."
        ),
    }


def _tool_names(listed) -> list:
    tools = getattr(listed, "tools", None)
    if tools is None and isinstance(listed, dict):
        tools = listed.get("tools")
    return list(tools or [])


async def _do_retry(server_url, headers, sanitized_suffix, arguments, cap, payment_required):
    from x402.mcp import x402MCPClient

    from ..coinbase_mcp.payment_client import CoinbaseMcpPaymentClient
    from ..mcp_client import McpSessionAdapter, open_session, with_timeout

    payment_client = CoinbaseMcpPaymentClient(max_price_usdc=cap)

    async with open_session(url=server_url, headers=headers) as session:
        listed = _tool_names(await with_timeout(session.list_tools()))
        real = resolve_upstream_name(listed, sanitized_suffix)
        if real is None:
            available = [t.get("name") if isinstance(t, dict) else getattr(t, "name", "") for t in listed]
            raise LookupError(
                f"no tool on the server matches {sanitized_suffix!r}; available: {available}"
            )

        x402_mcp = x402MCPClient(McpSessionAdapter(session), payment_client, auto_payment=True)
        try:
            if payment_required and config.trust_supplied_payment_required():
                # Opt-in fast path: trust the agent-supplied requirement and pay directly. Off by
                # default because a forged requirement could redirect a payment (R6).
                payload = await payment_client.create_payment_payload(payment_required)
                result = await with_timeout(x402_mcp.call_tool_with_payment(real, arguments or {}, payload))
                price = min_usdc_in_accepts(payment_required)
            else:
                # Default: re-probe the upstream in-process (probe -> 402 -> sign -> retry), so the
                # requirement we pay always comes from the real server, not the caller.
                result = await with_timeout(x402_mcp.call_tool(real, arguments or {}))
                price = payment_client.last_min_usdc
        except Exception as exc:
            if _is_no_matching_requirements_error(exc):
                raise StructuredToolError({
                    "error": "incompatible_scheme",
                    "detail": (
                        "No compatible payment requirement found. This MCP tool may only accept "
                        "Permit2, which is not supported by this wallet (EIP-3009 only). "
                        "Check the service's accepts[] list."
                    ),
                    "hint": (
                        "This wallet signs EIP-3009 (exact scheme) only. Choose a service whose "
                        "accepts[] includes an EIP-3009-compatible requirement "
                        "(assetTransferMethod absent or 'erc3009')."
                    ),
                }) from exc
            raise
        return result, price


def x402_retry_mcp_payment(args: dict, **kwargs) -> str:
    """Handler for ``x402_retry_mcp_payment``. Always returns a JSON string."""
    args = args or {}
    tool_name = args.get("tool_name")
    if not tool_name:
        return json.dumps({"error": "tool_name is required (the mcp_* tool that needs payment)"})
    arguments = args.get("arguments") or {}
    payment_required = args.get("payment_required") or None
    cap = effective_cap(args.get("max_price_usdc"))

    servers = _load_mcp_servers()
    try:
        server_name, sanitized_suffix, scfg = resolve_server(tool_name, servers)
    except KeyError as exc:
        # Self-correct the most common mistake: passing a Bazaar-*discovered* resource name
        # (x402_get_…) instead of the proxy tool that was actually called. Hand back a ready-to-run
        # call so the agent recovers in one step instead of bailing to x402_request.
        # Pass the already-loaded servers dict to avoid a redundant config read.
        if _looks_like_bazaar_resource(tool_name):
            return json.dumps(_bazaar_redirect(tool_name, arguments, servers))
        return json.dumps({"error": str(exc)})

    server_url = scfg.get("url")
    if not server_url:
        return json.dumps(
            {
                "error": f"server '{server_name}' has no url in mcp_servers config "
                "(stdio upstreams are not yet supported by x402_retry_mcp_payment)"
            }
        )
    headers = scfg.get("headers") if isinstance(scfg.get("headers"), dict) else None
    session_id = current_session_id(kwargs)

    def run():
        result, price = run_async(
            _do_retry(server_url, headers, sanitized_suffix, arguments, cap, payment_required)
        )

        # Distinguish "payment attempted" from "payment settled".
        # MCPToolCallResult.payment_made = SDK submitted a signed payload (signing occurred).
        # MCPToolCallResult.payment_response = not None means the facilitator confirmed settlement.
        payment_attempted = getattr(result, "payment_made", False)
        settle_response = getattr(result, "payment_response", None)
        tx = getattr(settle_response, "transaction", None) if settle_response is not None else None
        payment_settled = settle_response is not None

        is_error = getattr(result, "is_error", False)
        content = [c.get("text") if isinstance(c, dict) else str(c) for c in (result.content or [])]

        # Parse the content for a structured error body (e.g. another 402 from the proxy).
        content_parsed: dict | None = None
        if content:
            try:
                import json as _json
                content_parsed = _json.loads(content[0]) if isinstance(content[0], str) else None
            except Exception:
                pass

        if is_error:
            # Build a structured error the agent can reason about.
            server_error = extract_server_error(content_parsed)
            if isinstance(content_parsed, dict):
                # Detect a second 402 from the upstream (payment rejected by the proxy/facilitator).
                if content_parsed.get("accepts") or content_parsed.get("x402Version"):
                    server_error = server_error or "upstream returned another 402 (payment rejected by server/facilitator)"

            if payment_attempted and not payment_settled:
                # Payment was signed and submitted but the upstream server returned is_error=True
                # without a settlement receipt. The facilitator may have rejected the payment (no
                # funds moved) OR settled and the receipt was lost before the server errored —
                # we cannot confirm on-chain status from here. Surface as unknown_settlement so
                # the caller treats it the same way as a timeout mid-flight (R1: fail closed).
                raise UnknownSettlementError(
                    "payment payload was submitted but the upstream server returned an error and "
                    "did not include a settlement receipt; check cdp_payments or run "
                    "`hermes x402 reconcile` before retrying. "
                    + (f"Server error: {server_error}." if server_error else "")
                )
            else:
                raise StructuredToolError({
                    "error": "mcp_tool_error",
                    "detail": server_error or "upstream tool returned is_error=true",
                    "raw_content": content[:3],
                    "payment_attempted": payment_attempted,
                    "payment_settled": payment_settled,
                    "price_usdc": price if payment_attempted else None,
                    "tool_name": tool_name,
                })

        # A "successful" MCP result without settlement confirmation is unsafe to treat as
        # complete when a payment was attempted: money may have moved but no receipt arrived.
        if payment_attempted and not payment_settled:
            raise UnknownSettlementError(
                "payment payload was submitted but settlement was not confirmed by the upstream "
                "server/facilitator; check cdp_payments or run `hermes x402 reconcile` before retrying"
            )

        # Success path: only record to ledger when settlement is actually confirmed.
        if payment_settled:
            record_paid_call(
                kind="mcp", amount_usdc=price or 0.0, endpoint=server_name, transaction=tx,
                session_id=session_id,
            )

        out = {
            "content": content,
            "is_error": False,
            "payment_made": payment_attempted,
            "payment_settled": payment_settled,
            "transaction": tx,
            "price_usdc": price,
            "tool_name": tool_name,
        }
        return out, (price if payment_settled else None), tx

    return run_journaled(
        kind="mcp",
        endpoint=server_name,
        arguments={"tool": tool_name, "arguments": arguments},
        requirement=payment_required,
        idempotency_key=args.get("idempotency_key"),
        override=bool(args.get("override")),
        cap=cap,
        label="x402_retry_mcp_payment",
        session_id=session_id,
        run=run,
    )
