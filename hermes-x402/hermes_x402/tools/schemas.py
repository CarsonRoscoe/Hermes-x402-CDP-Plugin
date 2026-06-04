"""Tool schemas — what the LLM reads to decide when to call each x402 tool.

Descriptions are written for the model: state the capability and when to use it.
"""

from __future__ import annotations

X402_REQUEST = {
    "name": "x402_request",
    "description": (
        "Make an HTTP request to an x402-enabled endpoint, paying automatically with USDC "
        "from your wallet if the server responds with 402 Payment Required. Use this ONLY for a "
        "direct HTTP API whose URL you already know. Do NOT use it to call CDP Bazaar services — "
        "those are paid via mcp_bazaar_proxy_tool_call followed by x402_retry_mcp_payment, never "
        "by calling their URL directly. If payment is rejected due to insufficient balance, use "
        "mcp_coinbase_faucet_usdc (testnet) to fund the wallet first, then retry."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The endpoint URL to call."},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "description": "HTTP method (default GET).",
            },
            "headers": {"type": "object", "description": "Optional request headers."},
            "body": {"type": "string", "description": "Optional request body (JSON string)."},
            "max_price_usdc": {
                "type": "number",
                "description": "Refuse to pay more than this many USDC for the call.",
            },
            "idempotency_key": {
                "type": "string",
                "description": (
                    "Optional stable key making this a one-time operation: a repeat call with "
                    "the same key returns the prior paid result instead of paying again."
                ),
            },
            "override": {
                "type": "boolean",
                "description": (
                    "Set true only to deliberately retry an operation a prior attempt left "
                    "unconfirmed (may pay again)."
                ),
            },
        },
        "required": ["url"],
    },
}

X402_RETRY_MCP_PAYMENT = {
    "name": "x402_retry_mcp_payment",
    "description": (
        "Pay for and retry an mcp_* tool call that returned a payment-required (402) result. "
        "Set tool_name to the EXACT mcp_* tool you just invoked and pass the SAME arguments; it "
        "signs a USDC payment and re-issues that call, returning the paid result. "
        "IMPORTANT for CDP Bazaar services: the tool you invoked is the proxy, so set "
        "tool_name=\"mcp_bazaar_proxy_tool_call\" with arguments={toolName, parameters} — do NOT "
        "pass the discovered x402_… resource name (it is not a registered tool and will fail "
        "with 'no mcp_servers entry matches tool'). Do not call this preemptively; only after the "
        "same mcp_* tool returned payment-required in this session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": (
                    "The exact mcp_* tool you literally called that returned payment-required. "
                    "For a Bazaar service this is \"mcp_bazaar_proxy_tool_call\", NOT the "
                    "discovered x402_… resource name."
                ),
            },
            "arguments": {
                "type": "object",
                "description": (
                    "The same arguments you passed to the original mcp_* call. For a Bazaar proxy "
                    "retry this is {toolName: \"x402_…\", parameters: {…}} — identical to your "
                    "mcp_bazaar_proxy_tool_call args."
                ),
            },
            "payment_required": {
                "type": "object",
                "description": (
                    "Rarely needed — leave empty and the tool re-probes the server to discover the "
                    "requirement. Optional override: the structured payment-required details from "
                    "the failed call, if you happen to have them."
                ),
            },
            "max_price_usdc": {
                "type": "number",
                "description": "Refuse to pay more than this many USDC for the call.",
            },
            "idempotency_key": {
                "type": "string",
                "description": (
                    "Optional stable key making this a one-time operation: a repeat call with "
                    "the same key returns the prior paid result instead of paying again."
                ),
            },
            "override": {
                "type": "boolean",
                "description": (
                    "Set true only to deliberately retry an operation a prior attempt left "
                    "unconfirmed (may pay again)."
                ),
            },
        },
        "required": ["tool_name", "arguments"],
    },
}
