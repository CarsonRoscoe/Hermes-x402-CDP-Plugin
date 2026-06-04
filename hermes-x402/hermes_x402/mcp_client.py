"""Shared MCP client plumbing.

One place for: opening an MCP ``ClientSession`` over any transport (streamable HTTP, SSE,
or stdio), parsing a ``CallToolResult`` into a dict, and the thin ``McpSessionAdapter`` the
x402 SDK's ``x402MCPClient`` expects. Both the Coinbase MCP signer connection and the
``x402_retry_mcp_payment`` tool reuse this, so transport/parse logic lives in exactly one
module.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from . import config


async def with_timeout(coro, *, timeout: float | None = None):
    """Await ``coro`` under the configured per-operation network timeout.

    Bounds how long a slow or hostile MCP server can stall a paid flow. A ``TimeoutError``
    propagates to the caller (which fails closed before any payment is sent).
    """
    return await asyncio.wait_for(coro, timeout if timeout is not None else config.timeout_seconds())


def _http_streams(url: str, headers: dict | None):
    """Open a remote MCP transport.

    Prefer streamable HTTP; fall back to SSE only when the streamable-http client module is
    unavailable (older MCP SDKs). We intentionally catch only ``ImportError`` so genuine
    streamable-http construction errors surface instead of being silently misrouted to SSE.
    Protocol-level negotiation (a server that only speaks SSE) is out of scope here.
    """
    try:
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(url, headers=headers or None)
    except ImportError:
        from mcp.client.sse import sse_client

        sse_url = url if url.rstrip("/").endswith("/sse") else url.rstrip("/") + "/sse"
        return sse_client(sse_url, headers=headers or None)


def _stdio_streams(command: str, args: list | None):
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    return stdio_client(StdioServerParameters(command=command, args=list(args or [])))


@asynccontextmanager
async def open_session(
    *,
    url: str | None = None,
    headers: dict | None = None,
    command: str | None = None,
    args: list | None = None,
):
    """Yield an initialized ``mcp.ClientSession`` over the chosen transport.

    Provide either ``command`` (stdio) or ``url`` (remote). ``await`` directly from the
    shared event loop; do not nest ``run_async``.
    """
    from mcp import ClientSession

    if command:
        ctx = _stdio_streams(command, args)
    elif url:
        ctx = _http_streams(url, headers)
    else:
        raise ValueError("open_session requires either command (stdio) or url (remote)")

    async with ctx as streams:
        # stdio/sse yield (read, write); streamable-http yields a 3-tuple.
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await with_timeout(session.initialize())
            yield session


def first_text(result: Any) -> str | None:
    """Return the first text content item from a CallToolResult, if any."""
    for item in getattr(result, "content", None) or []:
        if hasattr(item, "text"):
            return item.text
        if isinstance(item, dict) and "text" in item:
            return item["text"]
    return None


def result_to_dict(result: Any) -> dict:
    """Extract a JSON object from a CallToolResult (structuredContent or text).

    Raises ``RuntimeError`` if the tool reported an error or no JSON could be recovered.
    """
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise RuntimeError(f"mcp tool error: {first_text(result) or 'unknown error'}")

    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict) and sc:
        return sc

    text = first_text(result)
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    raise RuntimeError("mcp tool returned no structured JSON result")


class _SdkResult:
    """Carries a tool result using the attribute names ``convert_mcp_result`` reads.

    The x402 SDK re-runs ``convert_mcp_result`` on whatever the adapter returns, and that
    helper reads ``content`` / ``isError`` / ``_meta`` / ``structuredContent``. Exposing
    exactly those names lets the structured payment-required body and the settlement meta
    (tx hash) survive the SDK's re-conversion instead of being silently dropped.
    """

    def __init__(self, content, isError, _meta, structuredContent) -> None:
        self.content = content
        self.isError = isError
        self._meta = _meta
        self.structuredContent = structuredContent


class McpSessionAdapter:
    """Adapter wrapping an ``mcp.ClientSession`` for the x402 SDK's ``x402MCPClient``.

    ``x402MCPClient`` calls ``call_tool(params, **kwargs)`` where ``params`` is a dict
    ``{"name", "arguments", "_meta"}``; this unpacks it onto the real session and returns a
    result shaped for the SDK's ``convert_mcp_result``.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    async def call_tool(self, params: dict, **kwargs: Any):
        result = await self._session.call_tool(
            name=params.get("name", ""),
            arguments=params.get("arguments") or {},
            meta=params.get("_meta"),
        )
        content = []
        for item in result.content:
            if hasattr(item, "text"):
                content.append({"type": "text", "text": item.text})
            else:
                content.append({"type": getattr(item, "type", "text"), "text": str(item)})
        meta = dict(result.meta) if getattr(result, "meta", None) else {}
        structured = getattr(result, "structuredContent", None)
        is_error = getattr(result, "isError", False) or getattr(result, "is_error", False)
        return _SdkResult(content=content, isError=is_error, _meta=meta, structuredContent=structured)

    async def list_tools(self):
        return await self._session.list_tools()
