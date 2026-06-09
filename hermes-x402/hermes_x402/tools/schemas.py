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

# --------------------------------------------------------------------------- #
# Local CDP wallet tools (provider == "local"). Self-custodial CDP server wallet.
# --------------------------------------------------------------------------- #

CDP_WALLET_STATUS = {
    "name": "cdp_wallet_status",
    "description": (
        "Show the self-custodial CDP server wallet: its address, account name, and the "
        "active network. Use to find the wallet address (e.g. to receive funds) or confirm "
        "the wallet is provisioned."
    ),
    "parameters": {"type": "object", "properties": {}},
}

CDP_WALLET_BALANCE = {
    "name": "cdp_wallet_balance",
    "description": (
        "Show on-chain token balances of the CDP server wallet: a balances[] list "
        "({symbol, amount, decimals, contract}) plus eth/usdc convenience values. Check this "
        "before making paid x402 calls or transfers to confirm the wallet is funded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "network": {
                "type": "string",
                "description": "Network to read (default: the configured x402.network).",
            },
            "asset": {
                "type": "string",
                "description": "Optional symbol filter, e.g. 'USDC' — limits balances[] to that token.",
            },
        },
    },
}

CDP_FAUCET = {
    "name": "cdp_faucet",
    "description": (
        "Request free TESTNET funds from the CDP faucet into the wallet. Testnet only "
        "(base-sepolia / ethereum-sepolia) — it errors on mainnet. Use this to fund the "
        "wallet on testnet instead of any browser/Circle/Alchemy faucet (no captcha). For "
        "mainnet funding use cdp_onramp."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token": {
                "type": "string",
                "enum": ["usdc", "eth"],
                "description": "Which testnet token to request (default: usdc).",
            },
            "network": {
                "type": "string",
                "description": "Testnet network (default: the configured x402.network).",
            },
        },
    },
}

CDP_ONRAMP = {
    "name": "cdp_onramp",
    "description": (
        "Generate a single-use Coinbase Onramp URL to buy crypto (USDC/ETH) with fiat and "
        "deliver it to the agent's wallet on a mainnet. Returns a URL the user opens to "
        "complete the purchase. Use for MAINNET funding (testnet uses cdp_faucet)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "asset": {
                "type": "string",
                "description": "Asset to buy, e.g. 'USDC' or 'ETH' (default: USDC).",
            },
            "network": {
                "type": "string",
                "description": "Delivery network, e.g. 'base' (default: the configured x402.network).",
            },
            "amount": {
                "type": "number",
                "description": "Optional fiat amount to spend (e.g. 25 for $25). Enables a priced quote.",
            },
            "currency": {
                "type": "string",
                "description": "Fiat currency for 'amount' (default: USD).",
            },
            "country": {
                "type": "string",
                "description": "ISO 3166-1 alpha-2 country (e.g. 'US'). Used with amount.",
            },
            "subdivision": {
                "type": "string",
                "description": "ISO 3166-2 state code (e.g. 'NY'). Required for US priced quotes.",
            },
        },
    },
}

CDP_PAYMENTS = {
    "name": "cdp_payments",
    "description": (
        "List recent x402 payments this wallet has made (receipts), newest first: endpoint, "
        "amount in USDC, tx hash, kind, network, and timestamp. Use to reconcile spend or "
        "confirm a paid call settled."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max payments to return (default 20, max 200).",
            },
            "since": {
                "type": "number",
                "description": "Optional Unix timestamp; only return payments at/after this time.",
            },
        },
    },
}

CDP_TRANSFER = {
    "name": "cdp_transfer",
    "description": (
        "Send USDC or ETH from the CDP server wallet to another address. This MOVES REAL "
        "FUNDS out of the wallet — use only when explicitly instructed. USDC transfers are "
        "capped by x402.max_price_usdc unless 'override' is set."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient 0x address."},
            "amount": {
                "type": "number",
                "description": "Human amount to send (e.g. 1.5 for 1.5 USDC).",
            },
            "token": {
                "type": "string",
                "enum": ["usdc", "eth"],
                "description": "Token to send (default: usdc).",
            },
            "network": {
                "type": "string",
                "description": "Network to send on (default: the configured x402.network).",
            },
            "override": {
                "type": "boolean",
                "description": "Set true to bypass the USDC per-call cap (x402.max_price_usdc).",
            },
        },
        "required": ["to", "amount"],
    },
}
