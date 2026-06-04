"""Stdio JSON-RPC MCP server — dev stand-in for the real Coinbase MCP.

Implements the Coinbase MCP tool contract plus two testnet-only faucet tools:

  create_payment_payload  — sign an x402 PaymentRequired → PaymentPayload
  coinbase_balance        — real on-chain balance via CDP list_token_balances
  coinbase_status         — wallet address + account name
  faucet_eth              — request testnet ETH from the CDP faucet (base-sepolia only)
  faucet_usdc             — request testnet USDC from the CDP faucet (base-sepolia only)

Requires CDP credentials: CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET.
Optional: CDP_ACCOUNT_NAME (default: hermes-x402), CDP_NETWORK (default: base-sepolia).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .signer import Signer, _network

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "coinbase-mcp-fake", "version": "0.0.1"}

_signer = Signer()

TOOLS = [
    {
        "name": "create_payment_payload",
        "description": (
            "Sign an x402 payment. Accepts x402 v1 or v2 PaymentRequired and lets the "
            "x402 SDK select an exact EVM (EIP-3009) requirement from accepts[]; "
            "unsupported schemes such as Permit2 are skipped. USDC requirements are "
            "preferred. Returns a signed PaymentPayload."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "payment_required": {
                    "type": "object",
                    "description": "x402 PaymentRequired (x402Version, resource, accepts[]).",
                }
            },
            "required": ["payment_required"],
        },
    },
    {
        "name": "coinbase_balance",
        "description": "Real on-chain ETH and USDC balance for the CDP server wallet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "network": {"type": "string", "description": "Network (default: base-sepolia)."}
            },
        },
    },
    {
        "name": "coinbase_status",
        "description": "CDP wallet address and account name.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "faucet_eth",
        "description": (
            "Request testnet ETH from the CDP faucet. Testnet only (base-sepolia). "
            "Limit: 0.0001 ETH per claim, 1000 claims per 24 h."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "network": {
                    "type": "string",
                    "description": "Testnet network (default: base-sepolia).",
                }
            },
        },
    },
    {
        "name": "faucet_usdc",
        "description": (
            "Request testnet USDC from the CDP faucet. Testnet only (base-sepolia). "
            "Limit: 1 USDC per claim, 10 claims per 24 h."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "network": {
                    "type": "string",
                    "description": "Testnet network (default: base-sepolia).",
                }
            },
        },
    },
]


def _ok(structured: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(structured)}],
        "structuredContent": structured,
        "isError": False,
    }


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _call_tool(name: str, args: dict[str, Any]) -> dict:
    try:
        if name == "create_payment_payload":
            pr = args.get("payment_required")
            if not isinstance(pr, dict):
                return _err("payment_required (object) is required")
            payload = _signer.create_payment_payload(pr)
            return _ok({"payment_payload": payload})

        if name == "coinbase_balance":
            network = args.get("network") or _network()
            bal = _signer.balance(network)
            return _ok(bal)

        if name == "coinbase_status":
            addr = _signer.address()
            return _ok({
                "address": addr,
                "account_name": os.getenv("CDP_ACCOUNT_NAME", "hermes-x402"),
                "network": _network(),
            })

        if name in ("faucet_eth", "faucet_usdc"):
            token = "eth" if name == "faucet_eth" else "usdc"
            network = args.get("network") or _network()
            tx_hash = _signer.faucet(token, network)
            explorer = f"https://sepolia.basescan.org/tx/{tx_hash}" if "sepolia" in network else tx_hash
            return _ok({"tx_hash": tx_hash, "token": token, "network": network, "explorer": explorer})

        return _err(f"unknown tool: {name}")

    except Exception as exc:  # never crash the server loop
        return _err(f"{type(exc).__name__}: {exc}")


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
    sys.stdout.flush()


def _handle(msg: dict) -> None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        _send({"id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {}},
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        _send({"id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not name:
            _send({"id": mid, "error": {"code": -32602, "message": "Missing tool name"}})
            return
        _send({"id": mid, "result": _call_tool(name, args)})
    elif method == "ping":
        _send({"id": mid, "result": {}})
    elif mid is not None:
        _send({"id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})


def main() -> int:
    sys.stderr.write("fake-coinbase-mcp ready (CDP server wallet)\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send({"id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        try:
            _handle(msg)
        except Exception as exc:
            if msg.get("id") is not None:
                _send({"id": msg["id"], "error": {"code": -32603, "message": str(exc)}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
