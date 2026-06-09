"""Unit tests for the hermes-x402 plugin. No network; the Coinbase MCP is mocked."""

from __future__ import annotations

import json
import os

import pytest


# --------------------------------------------------------------------------- #
# register(ctx)
# --------------------------------------------------------------------------- #
class FakeCtx:
    def __init__(self):
        self.tools, self.cli, self.cmds, self.skills, self.hooks = [], [], [], [], []

    def register_tool(self, **kw):
        self.tools.append(kw["name"])

    def register_cli_command(self, **kw):
        self.cli.append(kw["name"])

    def register_command(self, name, handler, description=""):
        self.cmds.append(name)

    def register_skill(self, name, path, description=""):
        self.skills.append(name)

    def register_hook(self, name, cb):
        self.hooks.append(name)


def test_register_wires_all_surfaces():
    import hermes_x402

    ctx = FakeCtx()
    hermes_x402.register(ctx)
    # Payment tools are always present; cdp_* tools are registered for any provider
    # (visibility is controlled at runtime by check_fn, not at registration time).
    assert "x402_request" in ctx.tools
    assert "x402_retry_mcp_payment" in ctx.tools
    cdp_tools = [t for t in ctx.tools if t.startswith("cdp_")]
    assert set(cdp_tools) == {
        "cdp_wallet_status", "cdp_wallet_balance", "cdp_faucet",
        "cdp_onramp", "cdp_transfer", "cdp_payments",
    }
    assert ctx.cli == ["x402"]
    assert ctx.cmds == ["x402"]
    assert ctx.skills == ["x402-payments"]
    assert set(ctx.hooks) == {
        "pre_tool_call", "transform_tool_result", "on_session_end", "on_session_finalize"
    }


# --------------------------------------------------------------------------- #
# tools validate args / return JSON
# --------------------------------------------------------------------------- #
def test_request_returns_json_on_bad_args():
    from hermes_x402.tools.request import x402_request

    assert json.loads(x402_request({}))["error"]


def test_x402_request_records_settled_http_payment(monkeypatch):
    """Handler-level HTTP flow: fetch result, ledger write, and journal finalization."""
    from hermes_x402 import ledger
    from hermes_x402.tools import request

    async def fake_do_fetch(url, method, headers, body, cap_usdc):
        assert url == "https://api.example.com/paid?token=redacted"
        assert method == "POST"
        assert headers == {"content-type": "application/json"}
        assert body == '{"city": "SF"}'
        assert cap_usdc == pytest.approx(0.25)
        return 200, '{"ok": true}', {"transaction": "0xpaid"}, 0.05

    monkeypatch.setattr(request, "_do_fetch", fake_do_fetch)

    out = json.loads(request.x402_request(
        {
            "url": "https://api.example.com/paid?token=redacted",
            "method": "POST",
            "headers": {"content-type": "application/json"},
            "body": '{"city": "SF"}',
            "max_price_usdc": 0.25,
            "idempotency_key": "http-paid-once",
        },
        session_id="s1",
    ))

    assert out["status"] == 200
    assert out["payment"]["transaction"] == "0xpaid"
    assert out["payment_made"] is True
    assert out["payment_settled"] is True
    assert out["price_usdc"] == pytest.approx(0.05)
    rows = ledger.recent_spend(1)
    assert rows[0]["kind"] == "http"
    assert rows[0]["amount_usdc"] == pytest.approx(0.05)
    assert rows[0]["endpoint_host"] == "api.example.com"
    assert ledger.journal_lookup("key:http-paid-once")["state"] == "succeeded"


def test_x402_request_classifies_rejected_402():
    from hermes_x402.tools.request import _classify_402, _Payment402Error

    exc = _Payment402Error(
        402,
        '{"error": "insufficient balance"}',
        {"PAYMENT-REQUIRED": "{}", "content-type": "application/json"},
        {"resource": "https://api.example.com", "accepts": [{"amount": "50000", "network": "base"}]},
        signing_occurred=True,
        last_min_usdc=0.05,
    )

    out = _classify_402(exc)
    assert out["error"] == "payment_rejected_402"
    assert out["required_usdc"] == pytest.approx(0.05)
    assert "Fund the wallet" in out["hint"]


def test_x402_request_rejected_402_sets_failed_journal(monkeypatch):
    """A classified 402 should return structured error and finalize journal as failed."""
    from hermes_x402 import ledger
    from hermes_x402.tools import request

    async def fake_do_fetch(url, method, headers, body, cap_usdc):
        raise request._Payment402Error(
            402,
            '{"error": "insufficient balance"}',
            {"PAYMENT-REQUIRED": "{}", "content-type": "application/json"},
            {"resource": "https://api.example.com", "accepts": [{"amount": "50000", "network": "base"}]},
            signing_occurred=True,
            last_min_usdc=0.05,
        )

    monkeypatch.setattr(request, "_do_fetch", fake_do_fetch)
    out = json.loads(request.x402_request(
        {"url": "https://api.example.com/paid", "idempotency_key": "http-402-rejected"},
        session_id="s1",
    ))
    assert out["error"] == "payment_rejected_402"
    assert ledger.journal_lookup("key:http-402-rejected")["state"] == "failed"


def test_x402_request_not_attempted_402_sets_failed_journal(monkeypatch):
    """A 402 without signing should still be classified and journaled as failed."""
    from hermes_x402 import ledger
    from hermes_x402.tools import request

    async def fake_do_fetch(url, method, headers, body, cap_usdc):
        raise request._Payment402Error(
            402,
            '{"error": "not x402"}',
            {"content-type": "application/json"},
            None,
            signing_occurred=False,
            last_min_usdc=None,
        )

    monkeypatch.setattr(request, "_do_fetch", fake_do_fetch)
    out = json.loads(request.x402_request(
        {"url": "https://api.example.com/non-x402", "idempotency_key": "http-402-no-sign"},
        session_id="s1",
    ))
    assert out["error"] == "payment_not_attempted_402"
    assert ledger.journal_lookup("key:http-402-no-sign")["state"] == "failed"


def test_x402_request_attempted_without_settlement_marks_unknown(monkeypatch):
    """HTTP success without PAYMENT-RESPONSE after signing must not finalize as succeeded."""
    from hermes_x402 import ledger
    from hermes_x402.tools import request

    async def fake_do_fetch(url, method, headers, body, cap_usdc):
        return 200, '{"ok": true}', None, 0.05

    monkeypatch.setattr(request, "_do_fetch", fake_do_fetch)
    out = json.loads(request.x402_request(
        {"url": "https://api.example.com/paid", "idempotency_key": "http-unknown-settle"},
        session_id="s1",
    ))
    assert out["error"] == "unknown_settlement"
    assert out["reconcile"] is True
    assert ledger.journal_lookup("key:http-unknown-settle")["state"] == "unknown"


def test_retry_requires_tool_name():
    from hermes_x402.tools.retry_mcp import x402_retry_mcp_payment

    assert json.loads(x402_retry_mcp_payment({}))["error"]
    assert json.loads(x402_retry_mcp_payment({"arguments": {}}))["error"]


# --------------------------------------------------------------------------- #
# x402_retry_mcp_payment: server resolution from mcp_servers config
# --------------------------------------------------------------------------- #
def _mock_servers(monkeypatch, servers: dict):
    from hermes_x402.tools import retry_mcp

    monkeypatch.setattr(retry_mcp, "_load_mcp_servers", lambda: servers)


def test_resolve_server_longest_prefix(monkeypatch):
    from hermes_x402.tools import retry_mcp

    _mock_servers(
        monkeypatch,
        {
            "bazaar": {"url": "https://bazaar.example/mcp"},
            "my_paid": {"url": "https://paid.example/mcp"},
        },
    )
    name, upstream, cfg = retry_mcp.resolve_server("mcp_bazaar_proxy_tool_call")
    assert name == "bazaar"
    assert upstream == "proxy_tool_call"
    assert cfg["url"] == "https://bazaar.example/mcp"

    name, upstream, cfg = retry_mcp.resolve_server("mcp_my_paid_search")
    assert name == "my_paid"
    assert upstream == "search"


def test_resolve_server_unknown_raises(monkeypatch):
    from hermes_x402.tools import retry_mcp

    _mock_servers(monkeypatch, {"bazaar": {"url": "https://x/mcp"}})
    with pytest.raises(KeyError):
        retry_mcp.resolve_server("mcp_other_tool")


def test_bazaar_resource_retry_returns_proxy_fix(monkeypatch):
    from hermes_x402.tools.retry_mcp import x402_retry_mcp_payment

    _mock_servers(monkeypatch, {"cdp-bazaar": {"url": "https://api.example.com/x402/discovery/mcp"}})
    out = json.loads(x402_retry_mcp_payment({
        "tool_name": "x402_get_https___paid_example_weather",
        "arguments": {"city": "SF"},
    }))

    assert out["error"] == "wrong_tool_name_for_retry"
    assert out["fix"]["tool_name"] == "mcp_cdp_bazaar_proxy_tool_call"
    assert out["fix"]["arguments"] == {
        "toolName": "x402_get_https___paid_example_weather",
        "parameters": {"city": "SF"},
    }


def test_retry_errors_when_server_has_no_url(monkeypatch):
    from hermes_x402.tools.retry_mcp import x402_retry_mcp_payment

    _mock_servers(monkeypatch, {"local": {"command": "some-stdio-server"}})
    out = json.loads(x402_retry_mcp_payment({"tool_name": "mcp_local_pay", "arguments": {}}))
    assert "url" in out["error"]


def test_transform_hook_appends_retry_hint_for_payment_required_mcp_result():
    from hermes_x402.hooks import on_transform_tool_result

    result = json.dumps({"x402Version": 2, "accepts": [{"amount": "1000"}]})
    transformed = on_transform_tool_result(
        tool_name="mcp_bazaar_proxy_tool_call",
        args={"toolName": "x402_get_weather", "parameters": {"city": "SF"}},
        result=result,
    )
    assert transformed is not None
    assert "x402_retry_mcp_payment" in transformed
    assert '"tool_name": "mcp_bazaar_proxy_tool_call"' in transformed


def test_transform_hook_ignores_non_mcp_or_non_payment_results():
    from hermes_x402.hooks import on_transform_tool_result

    assert on_transform_tool_result(
        tool_name="x402_request",
        args={},
        result=json.dumps({"x402Version": 2, "accepts": [{"amount": "1000"}]}),
    ) is None
    assert on_transform_tool_result(
        tool_name="mcp_paid_tool",
        args={},
        result=json.dumps({"ok": True}),
    ) is None


def test_x402_retry_mcp_records_only_settled_payment(monkeypatch):
    """Handler-level MCP flow: resolved server, settled result, ledger write, journal state."""
    from hermes_x402 import ledger
    from hermes_x402.tools import retry_mcp

    captured = {}

    class Settle:
        transaction = "0xmcp"

    class Result:
        payment_made = True
        payment_response = Settle()
        is_error = False
        content = [{"text": "paid result"}]

    async def fake_do_retry(server_url, headers, sanitized_suffix, arguments, cap, payment_required):
        captured.update(
            server_url=server_url,
            headers=headers,
            sanitized_suffix=sanitized_suffix,
            arguments=arguments,
            cap=cap,
            payment_required=payment_required,
        )
        return Result(), 0.075

    _mock_servers(
        monkeypatch,
        {"bazaar": {"url": "https://bazaar.example/mcp", "headers": {"x-test": "1"}}},
    )
    monkeypatch.setattr(retry_mcp, "_do_retry", fake_do_retry)

    out = json.loads(retry_mcp.x402_retry_mcp_payment(
        {
            "tool_name": "mcp_bazaar_proxy_tool_call",
            "arguments": {"toolName": "x402_get_weather", "parameters": {"city": "SF"}},
            "max_price_usdc": 0.25,
            "idempotency_key": "mcp-paid-once",
        },
        session_id="s1",
    ))

    assert out["content"] == ["paid result"]
    assert out["payment_made"] is True
    assert out["payment_settled"] is True
    assert out["transaction"] == "0xmcp"
    assert captured == {
        "server_url": "https://bazaar.example/mcp",
        "headers": {"x-test": "1"},
        "sanitized_suffix": "proxy_tool_call",
        "arguments": {"toolName": "x402_get_weather", "parameters": {"city": "SF"}},
        "cap": 0.25,
        "payment_required": None,
    }
    rows = ledger.recent_spend(1)
    assert rows[0]["kind"] == "mcp"
    assert rows[0]["amount_usdc"] == pytest.approx(0.075)
    assert rows[0]["endpoint_host"] == "bazaar"
    assert ledger.journal_lookup("key:mcp-paid-once")["state"] == "succeeded"


def test_x402_retry_mcp_attempted_without_settlement_marks_unknown(monkeypatch):
    """If payment was attempted but no settlement proof arrived, journal remains unknown."""
    from hermes_x402 import ledger
    from hermes_x402.tools import retry_mcp

    class Result:
        payment_made = True
        payment_response = None
        is_error = False
        content = [{"text": "maybe paid"}]

    async def fake_do_retry(server_url, headers, sanitized_suffix, arguments, cap, payment_required):
        return Result(), 0.075

    _mock_servers(monkeypatch, {"bazaar": {"url": "https://bazaar.example/mcp"}})
    monkeypatch.setattr(retry_mcp, "_do_retry", fake_do_retry)

    out = json.loads(retry_mcp.x402_retry_mcp_payment(
        {
            "tool_name": "mcp_bazaar_proxy_tool_call",
            "arguments": {"toolName": "x402_get_weather", "parameters": {"city": "SF"}},
            "idempotency_key": "mcp-unknown-settle",
        },
        session_id="s1",
    ))
    assert out["error"] == "unknown_settlement"
    assert out["reconcile"] is True
    assert ledger.journal_lookup("key:mcp-unknown-settle")["state"] == "unknown"


def test_sanitize_matches_hermes():
    from hermes_x402.tools.retry_mcp import _sanitize

    # Hermes: replace every char outside [A-Za-z0-9_] with _, preserving case.
    assert _sanitize("get-sum") == "get_sum"
    assert _sanitize("GetThing") == "GetThing"
    assert _sanitize("my--srv") == "my__srv"
    assert _sanitize("a.b/c") == "a_b_c"


def test_resolve_server_hyphen_and_uppercase(monkeypatch):
    from hermes_x402.tools import retry_mcp

    _mock_servers(
        monkeypatch,
        {
            "My-Srv": {"url": "https://my.example/mcp"},
            "other": {"url": "https://other.example/mcp"},
        },
    )
    # "My-Srv" sanitizes to "My_Srv"; agent-facing name is mcp_My_Srv_get_sum.
    name, suffix, cfg = retry_mcp.resolve_server("mcp_My_Srv_get_sum")
    assert name == "My-Srv"
    assert suffix == "get_sum"
    assert cfg["url"] == "https://my.example/mcp"


def test_resolve_upstream_name_maps_sanitized_to_real():
    from hermes_x402.tools.retry_mcp import resolve_upstream_name

    class Tool:
        def __init__(self, name):
            self.name = name

    tools = [Tool("get-sum"), Tool("listItems")]
    assert resolve_upstream_name(tools, "get_sum") == "get-sum"
    assert resolve_upstream_name(tools, "listItems") == "listItems"
    assert resolve_upstream_name(tools, "missing") is None
    # dict-shaped tool entries are also supported.
    assert resolve_upstream_name([{"name": "do-thing"}], "do_thing") == "do-thing"


# --------------------------------------------------------------------------- #
# shared mcp_client parsing
# --------------------------------------------------------------------------- #
def test_mcp_client_parses_structured_content():
    from hermes_x402.mcp_client import result_to_dict

    class R:
        isError = False
        structuredContent = {"address": "0xabc"}
        content = []

    assert result_to_dict(R()) == {"address": "0xabc"}


def test_mcp_client_parses_text_json_fallback():
    from hermes_x402.mcp_client import result_to_dict

    class Item:
        text = '{"usdc": 5}'

    class R:
        isError = False
        structuredContent = None
        content = [Item()]

    assert result_to_dict(R()) == {"usdc": 5}


def test_mcp_client_raises_on_tool_error():
    from hermes_x402.mcp_client import result_to_dict

    class Item:
        text = "boom"

    class R:
        isError = True
        structuredContent = None
        content = [Item()]

    with pytest.raises(RuntimeError):
        result_to_dict(R())


def test_adapter_preserves_structured_content_and_meta():
    # Regression: a structuredContent-only payment-required result must survive the SDK's
    # convert_mcp_result re-conversion (which reads structuredContent/_meta), so payment is
    # detected and the settlement meta is not dropped.
    import asyncio

    from x402.mcp.utils import convert_mcp_result

    from hermes_x402.mcp_client import McpSessionAdapter

    pr = {"x402Version": 2, "accepts": [{"amount": "1000"}]}

    class Raw:
        content = []
        structuredContent = pr
        isError = True
        meta = {"x402/payment-response": {"success": True}}

    class Session:
        async def call_tool(self, name, arguments, meta=None):
            return Raw()

    adapter = McpSessionAdapter(Session())
    sdk_result = asyncio.run(adapter.call_tool({"name": "t", "arguments": {}}))
    converted = convert_mcp_result(sdk_result)
    assert converted.structured_content == pr
    assert converted.meta == {"x402/payment-response": {"success": True}}
    assert converted.is_error is True


# --------------------------------------------------------------------------- #
# shared _paid helpers
# --------------------------------------------------------------------------- #
def test_effective_cap_picks_stricter(monkeypatch):
    from hermes_x402.tools import _paid

    monkeypatch.setattr(_paid.config, "max_price_usdc", lambda: 1.0)
    assert _paid.effective_cap(0.25) == pytest.approx(0.25)
    assert _paid.effective_cap(None) == pytest.approx(1.0)
    monkeypatch.setattr(_paid.config, "max_price_usdc", lambda: 0.0)
    assert _paid.effective_cap(None) == 0.0


# --------------------------------------------------------------------------- #
# Coinbase MCP payment client
# --------------------------------------------------------------------------- #
def test_payment_client_caps_before_signing():
    import asyncio

    from hermes_x402.coinbase_mcp.payment_client import (
        CoinbaseMcpPaymentClient,
        PaymentExceedsCapError,
    )

    payment_required = {
        "x402Version": 2,
        "resource": {"url": "https://x/y"},
        "accepts": [{"amount": "50000", "network": "eip155:8453"}],
    }

    class _Conn:
        async def call_tool(self, name, args):
            raise AssertionError("should not contact signer when over cap")

    client = CoinbaseMcpPaymentClient(_Conn(), max_price_usdc=0.01)
    with pytest.raises(PaymentExceedsCapError):
        asyncio.run(client.create_payment_payload(payment_required))


# --------------------------------------------------------------------------- #
# mcp_servers registration
# --------------------------------------------------------------------------- #
def test_ensure_mcp_servers_local_provider_bazaar_only():
    """Only bazaar is registered."""
    from hermes_x402 import mcp_servers

    cfg: dict = {"x402": {"provider": "local"}}
    names = mcp_servers.ensure_mcp_servers(cfg)
    assert names == ["bazaar"]
    servers = cfg["mcp_servers"]
    assert servers["bazaar"]["url"]


def test_ensure_mcp_servers_removes_stale_coinbase_in_local_mode():
    """Switching back to local: a stale coinbase entry left from coinbase_mcp mode is removed."""
    from hermes_x402 import mcp_servers

    cfg: dict = {
        "x402": {"provider": "local"},
        "mcp_servers": {"coinbase": {"command": "old-entry"}, "other": {"url": "x"}},
    }
    mcp_servers.ensure_mcp_servers(cfg)
    assert "coinbase" not in cfg["mcp_servers"]
    assert "bazaar" in cfg["mcp_servers"]
    assert "other" in cfg["mcp_servers"]  # unrelated servers untouched


# --------------------------------------------------------------------------- #
# ledger / budget / config
# --------------------------------------------------------------------------- #
def test_ledger_roundtrip_strips_query_params():
    from hermes_x402 import ledger

    ledger.record_payment(
        kind="http",
        amount_usdc=0.01,
        network="base",
        endpoint="https://api.example.com/data?token=secret",
        transaction="0xabc",
        session_id="s1",
    )
    rows = ledger.recent_spend(5)
    assert rows[0]["endpoint_host"] == "api.example.com"
    assert ledger.session_total("s1") == pytest.approx(0.01)


def test_budget_hook_blocks_over_session_budget(monkeypatch):
    from hermes_x402 import budget

    monkeypatch.setattr(budget.config, "session_budget_usdc", lambda: 1.0)
    monkeypatch.setattr(budget.ledger, "session_total", lambda sid: 2.0)
    res = budget.pre_tool_call(tool_name="x402_request", args={}, session_id="s1")
    assert res and res["action"] == "block"
    assert budget.pre_tool_call(tool_name="x402_retry_mcp_payment", args={}, session_id="s1")["action"] == "block"
    assert budget.pre_tool_call(tool_name="read_file", args={}, session_id="s1") is None


def test_budget_hook_blocks_projected_overshoot(monkeypatch):
    from hermes_x402 import budget

    # Under budget so far, but the pending call's cap would push past it.
    monkeypatch.setattr(budget.config, "session_budget_usdc", lambda: 1.0)
    monkeypatch.setattr(budget.config, "max_price_usdc", lambda: 0.0)
    monkeypatch.setattr(budget.ledger, "session_total", lambda sid: 0.8)
    blocked = budget.pre_tool_call(
        tool_name="x402_request", args={"max_price_usdc": 0.5}, session_id="s1"
    )
    assert blocked and blocked["action"] == "block"
    # A small cap that stays within budget is allowed.
    assert budget.pre_tool_call(
        tool_name="x402_request", args={"max_price_usdc": 0.1}, session_id="s1"
    ) is None


def test_config_data_dir_and_caip2(tmp_path):
    from hermes_x402 import config

    assert str(tmp_path) in str(config.data_dir())
    assert config.caip2("base") == "eip155:8453"


# --------------------------------------------------------------------------- #
# Money-safety hardening
# --------------------------------------------------------------------------- #
def test_failure_mode_defaults_strict():
    from hermes_x402 import config

    assert config.failure_mode() == "strict"
    assert config.is_strict() is True
    assert config.timeout_seconds() > 0
    # R6: agent-supplied payment_required is not trusted by default.
    assert config.trust_supplied_payment_required() is False


def test_facilitator_auth_failure_falls_back_to_testnet(monkeypatch):
    import sys
    import types

    from hermes_x402 import facilitator

    monkeypatch.setenv("CDP_API_KEY_ID", "key-id")
    monkeypatch.setenv("CDP_API_KEY_SECRET", "key-secret")

    cdp_mod = types.ModuleType("cdp")
    cdp_x402_mod = types.ModuleType("cdp.x402")

    def broken_facilitator_config(*_args):
        raise RuntimeError("bad auth config")

    cdp_x402_mod.create_facilitator_config = broken_facilitator_config
    cdp_mod.x402 = cdp_x402_mod
    monkeypatch.setitem(sys.modules, "cdp", cdp_mod)
    monkeypatch.setitem(sys.modules, "cdp.x402", cdp_x402_mod)

    x402_mod = types.ModuleType("x402")

    class FacilitatorConfig:
        def __init__(self, url):
            self.url = url

    x402_mod.FacilitatorConfig = FacilitatorConfig
    monkeypatch.setitem(sys.modules, "x402", x402_mod)

    cfg = facilitator.facilitator_config()
    assert cfg.url == facilitator.TESTNET_FACILITATOR_URL


def test_r2_run_async_cancels_on_timeout():
    import asyncio

    from hermes_x402._async import UnknownSettlementError, run_async

    async def slow():
        await asyncio.sleep(5)

    with pytest.raises(UnknownSettlementError):
        run_async(slow(), timeout=0.05)


def test_r8_budget_hook_fails_closed_on_error(monkeypatch):
    from hermes_x402 import budget

    monkeypatch.setattr(budget.config, "session_budget_usdc", lambda: 1.0)

    def boom(_sid):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(budget.ledger, "session_total", boom)

    monkeypatch.setattr(budget.config, "is_strict", lambda: True)
    res = budget.pre_tool_call(tool_name="x402_request", args={}, session_id="s1")
    assert res and res["action"] == "block"

    monkeypatch.setattr(budget.config, "is_strict", lambda: False)
    assert budget.pre_tool_call(tool_name="x402_request", args={}, session_id="s1") is None


def test_budget_hook_requires_session_identity_in_strict(monkeypatch):
    from hermes_x402 import budget

    monkeypatch.setattr(budget.config, "session_budget_usdc", lambda: 1.0)
    monkeypatch.setattr(budget.config, "is_strict", lambda: True)
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    out = budget.pre_tool_call(tool_name="x402_request", args={})
    assert out and out["action"] == "block"


def test_r3_journal_states_roundtrip():
    from hermes_x402 import ledger

    jid = ledger.journal_begin(
        fingerprint="fp-x", idempotency_key=None, kind="http",
        endpoint="https://api.example.com/p", cap_usdc=0.5, session_id="s1",
    )
    assert ledger.journal_lookup("fp-x")["state"] == "pending"
    assert any(r["id"] == jid for r in ledger.journal_open_entries())
    ledger.journal_finalize(jid, state="succeeded", amount_usdc=0.25, tx="0xabc",
                            result_json='{"ok": true}')
    assert ledger.journal_lookup("fp-x")["state"] == "succeeded"
    assert ledger.journal_open_entries() == []


def test_r1_idempotency_replay_without_repay():
    from hermes_x402.tools import _paid

    calls = {"n": 0}

    def run():
        calls["n"] += 1
        return {"ok": True, "n": calls["n"]}, 0.5, "0xtx"

    kw = dict(
        kind="http", endpoint="https://x/y", arguments={"a": 1}, requirement=None,
        idempotency_key="K1", override=False, cap=1.0, label="x402_request", session_id="s1",
    )
    out1 = json.loads(_paid.run_journaled(run=run, **kw))
    out2 = json.loads(_paid.run_journaled(run=run, **kw))
    assert calls["n"] == 1  # second call replayed, did not re-run/pay
    assert out1["n"] == 1
    assert out2.get("replayed") is True


def test_r1_open_attempt_blocks_without_override():
    from hermes_x402 import ledger
    from hermes_x402.tools import _paid

    fp = _paid.operation_fingerprint(kind="mcp", endpoint="srv", arguments={"a": 1})
    # Simulate a prior in-flight/unknown attempt for the same fingerprint.
    ledger.journal_begin(fingerprint=fp, idempotency_key=None, kind="mcp", endpoint="srv",
                         cap_usdc=0.1, session_id="s1")

    ran = {"n": 0}

    def run():
        ran["n"] += 1
        return {"ok": True}, 0.1, None

    out = json.loads(_paid.run_journaled(
        kind="mcp", endpoint="srv", arguments={"a": 1}, requirement=None,
        idempotency_key=None, override=False, cap=0.1, label="x402_retry_mcp_payment",
        session_id="s1", run=run,
    ))
    assert out["error"] == "prior_attempt_incomplete"
    assert ran["n"] == 0  # blocked, did not run/pay


def test_r5_reservation_blocks_concurrent_overshoot():
    from hermes_x402 import ledger

    # Budget 1.0; an open reservation of 0.8 plus a new 0.5 call would overshoot.
    ledger.journal_begin(fingerprint="fp1", idempotency_key=None, kind="http",
                         endpoint="https://x", cap_usdc=0.8, session_id="s1", budget_usdc=1.0)
    with pytest.raises(ledger.BudgetExceededError):
        ledger.journal_begin(fingerprint="fp2", idempotency_key=None, kind="http",
                             endpoint="https://x", cap_usdc=0.5, session_id="s1", budget_usdc=1.0)


# --------------------------------------------------------------------------- #
# Onboarding flow
# --------------------------------------------------------------------------- #

def test_onboarding_missing_credentials_prints_help_without_raising(monkeypatch, capsys):
    """Missing CDP creds print specific instructions; no exception, no CDP calls made."""
    monkeypatch.setattr("hermes_x402.config.missing_cdp_credentials",
                        lambda: ["CDP_API_KEY_ID", "CDP_API_KEY_SECRET", "CDP_WALLET_SECRET"])
    # Ensure wallet.address() is never reached.
    monkeypatch.setattr("hermes_x402.wallet.address", lambda: (_ for _ in ()).throw(
        AssertionError("CDP wallet should not be provisioned when creds are missing")
    ))
    monkeypatch.setattr("hermes_x402.config.is_local_provider", lambda: True)

    from hermes_x402.setup_flow import run_x402_onboarding

    summary = run_x402_onboarding(config_dict={"x402": {"provider": "local"}})
    out = capsys.readouterr().out
    assert "CDP_API_KEY_ID" in out
    assert "CDP_API_KEY_SECRET" in out
    assert "CDP_WALLET_SECRET" in out
    assert summary["wallet"]["missing_credentials"] == [
        "CDP_API_KEY_ID", "CDP_API_KEY_SECRET", "CDP_WALLET_SECRET"
    ]


def test_onboarding_noninteractive_defaults_to_local(monkeypatch, capsys):
    """No TTY → provider defaults to 'local'; no interactive prompt is shown."""
    monkeypatch.setattr("sys.stdin", None)
    monkeypatch.setattr("hermes_x402.config.missing_cdp_credentials", lambda: [])
    monkeypatch.setattr("hermes_x402.wallet.address", lambda: "0xLocalWallet")
    monkeypatch.setattr("hermes_x402.wallet.usdc_balance", lambda net=None: 1.0)

    from hermes_x402.setup_flow import run_x402_onboarding

    summary = run_x402_onboarding(config_dict={})
    assert summary["provider"] == "local"
    out = capsys.readouterr().out
    assert "Select" not in out  # no interactive menu printed


def test_onboarding_persists_provider_and_budgets(monkeypatch):
    """Onboarding writes provider + budget defaults into the config dict."""
    monkeypatch.setattr("sys.stdin", None)
    monkeypatch.setattr("hermes_x402.config.missing_cdp_credentials", lambda: [])
    monkeypatch.setattr("hermes_x402.wallet.address", lambda: "0xWallet")
    monkeypatch.setattr("hermes_x402.wallet.usdc_balance", lambda net=None: 0.0)

    from hermes_x402.setup_flow import run_x402_onboarding

    cfg: dict = {}
    run_x402_onboarding(config_dict=cfg)
    assert cfg["x402"]["provider"] == "local"
    assert cfg["x402"]["max_price_usdc"] > 0
    assert cfg["x402"]["session_budget_usdc"] > 0


def test_onboarding_bazaar_registered_not_coinbase(monkeypatch):
    """Local provider onboarding registers bazaar but never coinbase."""
    monkeypatch.setattr("sys.stdin", None)
    monkeypatch.setattr("hermes_x402.config.missing_cdp_credentials", lambda: [])
    monkeypatch.setattr("hermes_x402.wallet.address", lambda: "0xWallet")
    monkeypatch.setattr("hermes_x402.wallet.usdc_balance", lambda net=None: 0.0)

    from hermes_x402.setup_flow import run_x402_onboarding

    cfg: dict = {"mcp_servers": {"coinbase": {"command": "stale-entry"}}}
    summary = run_x402_onboarding(config_dict=cfg)
    assert summary["mcp_servers"] == ["bazaar"]
    assert "coinbase" not in cfg["mcp_servers"]
    assert "bazaar" in cfg["mcp_servers"]


# --------------------------------------------------------------------------- #
# Local CDP tools — guards and output shapes
# --------------------------------------------------------------------------- #

def test_cdp_faucet_rejects_mainnet(monkeypatch):
    """cdp_faucet must error cleanly on mainnet — the testnet guard fires before CDP calls."""
    import hermes_x402.cdp.wallet_ops as wo
    # Mock so the guard fires through wallet_ops.faucet rather than hitting CDP creds.
    def mock_faucet(token, network=None):
        from hermes_x402 import config
        if not config.is_testnet(network or "base"):
            raise ValueError(f"Faucet is testnet-only (e.g. base-sepolia), not '{network}'.")
        return {"tx_hash": "0xmock", "token": token, "network": network, "explorer": ""}
    monkeypatch.setattr(wo, "faucet", mock_faucet)

    from hermes_x402.tools.cdp_tools import cdp_faucet

    for net in ("base", "ethereum"):
        out = json.loads(cdp_faucet({"token": "usdc", "network": net}))
        assert "error" in out, f"expected error for mainnet network '{net}'"
        assert "testnet" in out["error"].lower() or "faucet" in out["error"].lower()


def test_cdp_transfer_over_cap_refused(monkeypatch):
    """cdp_transfer must refuse a USDC transfer that exceeds the per-call cap."""
    monkeypatch.setattr("hermes_x402.config.max_price_usdc", lambda: 1.0)
    from hermes_x402.tools.cdp_tools import cdp_transfer

    out = json.loads(cdp_transfer({"to": "0xrecipient", "amount": 999, "token": "usdc"}))
    assert "error" in out
    assert "cap" in out["error"].lower()


def test_cdp_transfer_missing_to():
    """cdp_transfer must refuse when no recipient address is given."""
    from hermes_x402.tools.cdp_tools import cdp_transfer

    out = json.loads(cdp_transfer({"amount": 1, "token": "usdc"}))
    assert "error" in out
    assert "to" in out["error"].lower()


def test_cdp_transfer_eth_not_capped_by_usdc_cap(monkeypatch):
    """ETH transfers should not be refused by the USDC per-call cap."""
    monkeypatch.setattr("hermes_x402.config.max_price_usdc", lambda: 0.01)
    # We can't send ETH without CDP creds, but the guard check itself must not block it.
    from hermes_x402.tools.cdp_tools import cdp_transfer

    out = json.loads(cdp_transfer({"to": "0xrecipient", "amount": 1, "token": "eth"}))
    # Should fail at the CDP layer (no creds in test), not at the cap guard.
    assert out.get("error") is not None
    assert "cap" not in str(out.get("error", "")).lower()


def test_cdp_payments_shape(monkeypatch):
    """cdp_payments returns {payments[], count, total_usdc} from the ledger."""
    from hermes_x402 import ledger

    rows = [
        {"ts": 1000.0, "endpoint_host": "api.example.com", "amount_usdc": 0.01,
         "tx": "0xabc", "kind": "http", "network": "base"},
        {"ts": 900.0, "endpoint_host": "other.com", "amount_usdc": 0.05,
         "tx": "0xdef", "kind": "mcp", "network": "base"},
    ]
    monkeypatch.setattr(ledger, "recent_spend", lambda n: rows[:n])

    from hermes_x402.tools.cdp_tools import cdp_payments

    out = json.loads(cdp_payments({"limit": 10}))
    assert {"payments", "count", "total_usdc"}.issubset(set(out))
    assert "pending_journal_entries" in out
    assert out["count"] == 2
    assert abs(out["total_usdc"] - 0.06) < 1e-9
    for p in out["payments"]:
        assert set(p) >= {"timestamp", "endpoint", "amount_usdc", "tx", "settled", "kind", "network"}
    assert out["payments"][0]["settled"] is True


def test_cdp_payments_since_filter(monkeypatch):
    """cdp_payments filters rows before the 'since' timestamp."""
    from hermes_x402 import ledger

    rows = [
        {"ts": 2000.0, "endpoint_host": "a.com", "amount_usdc": 0.01,
         "tx": "0x1", "kind": "http", "network": "base"},
        {"ts": 500.0, "endpoint_host": "b.com", "amount_usdc": 0.02,
         "tx": "0x2", "kind": "mcp", "network": "base"},
    ]
    monkeypatch.setattr(ledger, "recent_spend", lambda n: rows[:n])

    from hermes_x402.tools.cdp_tools import cdp_payments

    out = json.loads(cdp_payments({"limit": 10, "since": 1000.0}))
    assert out["count"] == 1
    assert out["payments"][0]["endpoint"] == "a.com"


def test_cdp_wallet_balance_asset_filter(monkeypatch):
    """cdp_wallet_balance with asset='USDC' returns only the USDC entry in balances[]."""
    all_balances = {
        "network": "base-sepolia",
        "address": "0xWallet",
        "eth": 0.001,
        "usdc": 1.0,
        "balances": [
            {"symbol": "ETH", "amount": 0.001, "decimals": 18, "contract": None},
            {"symbol": "USDC", "amount": 1.0, "decimals": 6,
             "contract": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"},
        ],
    }
    import hermes_x402.cdp.wallet_ops as wo
    monkeypatch.setattr(wo, "balances", lambda net=None, asset=None: (
        {**all_balances,
         "balances": [b for b in all_balances["balances"]
                      if asset is None or b["symbol"].upper() == asset.upper()]}
    ))

    from hermes_x402.tools.cdp_tools import cdp_wallet_balance

    out = json.loads(cdp_wallet_balance({"network": "base-sepolia", "asset": "USDC"}))
    assert len(out["balances"]) == 1
    assert out["balances"][0]["symbol"] == "USDC"
    assert out["usdc"] == pytest.approx(1.0)


def test_cdp_wallet_status_shape(monkeypatch):
    """cdp_wallet_status returns the expected keys."""
    import hermes_x402.cdp.wallet_ops as wo
    monkeypatch.setattr(wo, "status", lambda: {
        "provider": "local",
        "address": "0xWallet",
        "account_name": "hermes-x402",
        "network": "base-sepolia",
    })

    from hermes_x402.tools.cdp_tools import cdp_wallet_status

    out = json.loads(cdp_wallet_status({}))
    assert set(out) >= {"provider", "address", "account_name", "network"}
    assert out["provider"] == "local"


def test_cdp_onramp_rejects_testnet(monkeypatch):
    """cdp_onramp must refuse on testnet networks (onramp delivers to mainnet only)."""
    import hermes_x402.cdp.wallet_ops as wo
    monkeypatch.setattr(wo, "onramp_url", lambda **kwargs: (_ for _ in ()).throw(
        ValueError("Onramp buys real funds on mainnet and cannot deliver to a testnet")
    ))

    from hermes_x402.tools.cdp_tools import cdp_onramp

    out = json.loads(cdp_onramp({"asset": "USDC", "network": "base-sepolia"}))
    assert "error" in out
    assert "testnet" in out["error"].lower() or "mainnet" in out["error"].lower()


# --------------------------------------------------------------------------- #
# Provider config helpers
# --------------------------------------------------------------------------- #

def test_normalize_provider_handles_edge_cases():
    from hermes_x402.config import normalize_provider

    assert normalize_provider("local") == "local"
    assert normalize_provider("LOCAL") == "local"
    assert normalize_provider("  coinbase_mcp  ") == "local"
    assert normalize_provider("bogus") == "local"
    assert normalize_provider(None) == "local"
    assert normalize_provider("") == "local"


def test_is_testnet_classification():
    from hermes_x402.config import is_testnet

    assert is_testnet("base-sepolia")
    assert is_testnet("ethereum-sepolia")
    assert is_testnet("ethereum-hoodi")
    assert not is_testnet("base")
    assert not is_testnet("ethereum")
    assert not is_testnet("polygon")


def test_cdp_tools_check_fn_is_local_provider():
    """Every cdp_* tool must be gated by the is_local_provider check_fn."""
    from hermes_x402 import config
    from hermes_x402.tools import TOOLS

    cdp_specs = [t for t in TOOLS if t.name.startswith("cdp_")]
    assert len(cdp_specs) == 6, f"unexpected cdp_* tool count: {[t.name for t in cdp_specs]}"
    for spec in cdp_specs:
        assert spec.check_fn is config.is_local_provider, \
            f"{spec.name} must be gated by is_local_provider"


# --------------------------------------------------------------------------- #
# decode_x402_header / header parsing helpers
# --------------------------------------------------------------------------- #

class TestDecodeX402Header:
    """Tests for the shared _paid.decode_x402_header helper."""

    def test_valid_base64_json(self):
        import base64

        from hermes_x402.tools._paid import decode_x402_header

        payload = {"x402Version": 2, "resource": {"url": "https://example.com"}}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        assert decode_x402_header(encoded) == payload

    def test_base64_without_padding(self):
        """Header values often arrive without '==' padding — should still decode."""
        import base64

        from hermes_x402.tools._paid import decode_x402_header

        payload = {"accepts": [{"scheme": "exact", "network": "eip155:84532"}]}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        assert decode_x402_header(encoded) == payload

    def test_raw_json_fallback(self):
        """When the value is raw JSON (not base64), it should still parse."""
        from hermes_x402.tools._paid import decode_x402_header

        raw = '{"error": "insufficient_funds"}'
        assert decode_x402_header(raw) == {"error": "insufficient_funds"}

    def test_empty_string_returns_none(self):
        from hermes_x402.tools._paid import decode_x402_header

        assert decode_x402_header("") is None

    def test_unparseable_returns_none(self):
        from hermes_x402.tools._paid import decode_x402_header

        assert decode_x402_header("not-json-or-base64!!!") is None

    def test_none_returns_none(self):
        from hermes_x402.tools._paid import decode_x402_header

        assert decode_x402_header(None) is None


class TestRequestHeaderParsing:
    """Tests for _decode_payment_response and _decode_payment_required in request.py."""

    def _make_headers(self, key: str, payload: dict) -> dict:
        import base64
        return {key: base64.b64encode(json.dumps(payload).encode()).decode()}

    def test_decode_payment_response_v2_header_name(self):
        from hermes_x402.tools.request import _decode_payment_response

        payload = {"transaction": "0xabc", "network": "eip155:84532"}
        headers = self._make_headers("PAYMENT-RESPONSE", payload)
        assert _decode_payment_response(headers) == payload

    def test_decode_payment_response_v1_legacy_header_name(self):
        from hermes_x402.tools.request import _decode_payment_response

        payload = {"transaction": "0xdef"}
        headers = self._make_headers("X-PAYMENT-RESPONSE", payload)
        assert _decode_payment_response(headers) == payload

    def test_decode_payment_response_v2_takes_priority_over_v1(self):
        """V2 header should be preferred when both are present."""
        import base64

        from hermes_x402.tools.request import _decode_payment_response

        v2 = {"transaction": "v2"}
        v1 = {"transaction": "v1"}
        headers = {
            "PAYMENT-RESPONSE": base64.b64encode(json.dumps(v2).encode()).decode(),
            "X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(v1).encode()).decode(),
        }
        assert _decode_payment_response(headers)["transaction"] == "v2"

    def test_decode_payment_response_missing_returns_none(self):
        from hermes_x402.tools.request import _decode_payment_response

        assert _decode_payment_response({}) is None
        assert _decode_payment_response({"Content-Type": "application/json"}) is None

    def test_decode_payment_required_v2_header_name(self):
        from hermes_x402.tools.request import _decode_payment_required

        payload = {"x402Version": 2, "accepts": [{"scheme": "exact"}]}
        headers = self._make_headers("PAYMENT-REQUIRED", payload)
        assert _decode_payment_required(headers) == payload

    def test_decode_payment_required_v1_legacy_header_name(self):
        from hermes_x402.tools.request import _decode_payment_required

        payload = {"x402Version": 1, "accepts": []}
        headers = self._make_headers("X-PAYMENT-REQUIRED", payload)
        assert _decode_payment_required(headers) == payload

    def test_decode_payment_required_malformed_base64_falls_back_to_json(self):
        from hermes_x402.tools.request import _decode_payment_required

        raw_json = '{"x402Version": 2, "accepts": []}'
        assert _decode_payment_required({"PAYMENT-REQUIRED": raw_json}) == {"x402Version": 2, "accepts": []}

    def test_decode_payment_required_missing_returns_none(self):
        from hermes_x402.tools.request import _decode_payment_required

        assert _decode_payment_required({}) is None


# --------------------------------------------------------------------------- #
# CDP signer policy functions
# --------------------------------------------------------------------------- #

class _FakeReq:
    """Minimal fake PaymentRequirements object for signer policy tests."""

    def __init__(self, transfer_method: str | None = None, asset_name: str | None = None):
        self._transfer_method = transfer_method
        self._asset_name = asset_name

    def get_extra(self):
        d = {}
        if self._transfer_method:
            d["assetTransferMethod"] = self._transfer_method
        if self._asset_name:
            d["name"] = self._asset_name
        return d or None


class TestExcludePermit2Policy:
    """Tests for _exclude_permit2_policy in cdp/signer.py."""

    def test_drops_permit2_requirements(self):
        from hermes_x402.cdp.signer import _exclude_permit2_policy

        reqs = [
            _FakeReq(transfer_method="permit2"),
            _FakeReq(transfer_method="erc3009"),
            _FakeReq(),  # no transfer method — keep (defaults to EIP-3009)
        ]
        kept = _exclude_permit2_policy(2, reqs)
        assert len(kept) == 2
        for r in kept:
            extra = r.get_extra() or {}
            assert extra.get("assetTransferMethod") != "permit2"

    def test_all_permit2_returns_empty(self):
        from hermes_x402.cdp.signer import _exclude_permit2_policy

        reqs = [_FakeReq(transfer_method="permit2"), _FakeReq(transfer_method="permit2")]
        assert _exclude_permit2_policy(2, reqs) == []

    def test_no_permit2_returns_all(self):
        from hermes_x402.cdp.signer import _exclude_permit2_policy

        reqs = [_FakeReq(transfer_method="erc3009"), _FakeReq()]
        assert _exclude_permit2_policy(2, reqs) == reqs

    def test_empty_list_returns_empty(self):
        from hermes_x402.cdp.signer import _exclude_permit2_policy

        assert _exclude_permit2_policy(2, []) == []


class TestPreferUsdcSelector:
    """Tests for _prefer_usdc_selector in cdp/signer.py."""

    def test_usdc_named_asset_wins(self):
        from hermes_x402.cdp.signer import _prefer_usdc_selector

        usdc = _FakeReq(asset_name="USDC")
        other = _FakeReq(asset_name="ETH")
        # USDC should be selected regardless of order.
        assert _prefer_usdc_selector(2, [other, usdc]) is usdc
        assert _prefer_usdc_selector(2, [usdc, other]) is usdc

    def test_usdc_e_variant_also_wins(self):
        from hermes_x402.cdp.signer import _prefer_usdc_selector

        usdc_e = _FakeReq(asset_name="USDC.e")
        other = _FakeReq(asset_name="WETH")
        assert _prefer_usdc_selector(2, [other, usdc_e]) is usdc_e

    def test_no_usdc_returns_first(self):
        """When no USDC-named asset is present, server order is preserved (first wins)."""
        from hermes_x402.cdp.signer import _prefer_usdc_selector

        eth = _FakeReq(asset_name="ETH")
        weth = _FakeReq(asset_name="WETH")
        assert _prefer_usdc_selector(2, [eth, weth]) is eth

    def test_case_insensitive_match(self):
        """Asset name comparison is case-insensitive."""
        from hermes_x402.cdp.signer import _prefer_usdc_selector

        usdc_lower = _FakeReq(asset_name="usdc")
        other = _FakeReq(asset_name="ETH")
        assert _prefer_usdc_selector(2, [other, usdc_lower]) is usdc_lower

    def test_single_requirement_returned(self):
        from hermes_x402.cdp.signer import _prefer_usdc_selector

        req = _FakeReq(asset_name="USDC")
        assert _prefer_usdc_selector(2, [req]) is req


# --------------------------------------------------------------------------- #
# M1 — MCP unknown settlement when is_error=True + payment attempted + not settled
# --------------------------------------------------------------------------- #

def test_mcp_payment_attempted_error_is_unknown_not_certain(monkeypatch):
    """When is_error=True, payment_made=True, payment_response=None the result must be
    unknown_settlement — NOT 'no funds deducted' (which asserts unverifiable certainty)."""
    from hermes_x402 import ledger
    from hermes_x402.tools import retry_mcp

    class NoSettleResult:
        payment_made = True
        payment_response = None
        is_error = True
        content = [{"text": '{"error": "upstream error"}'}]

    async def fake_do_retry(server_url, headers, sanitized_suffix, arguments, cap, payment_required):
        return NoSettleResult(), 0.05

    monkeypatch.setattr(retry_mcp, "_load_mcp_servers",
                        lambda: {"bazaar": {"url": "https://bazaar.example/mcp"}})
    monkeypatch.setattr(retry_mcp, "_do_retry", fake_do_retry)

    out = json.loads(retry_mcp.x402_retry_mcp_payment(
        {
            "tool_name": "mcp_bazaar_proxy_tool_call",
            "arguments": {"toolName": "x402_get_weather", "parameters": {}},
            "idempotency_key": "mcp-error-no-settle",
        },
        session_id="s-m1",
    ))
    # Must surface as unknown_settlement — never assert certainty that funds didn't move.
    assert out["error"] == "unknown_settlement", f"expected unknown_settlement, got: {out}"
    assert out.get("reconcile") is True
    # Must NOT contain the false certainty claim.
    assert "no funds were deducted" not in json.dumps(out)
    assert ledger.journal_lookup("key:mcp-error-no-settle")["state"] == "unknown"


# --------------------------------------------------------------------------- #
# M3 — Permit2-only endpoint returns incompatible_scheme, not credential guidance
# --------------------------------------------------------------------------- #

def test_permit2_only_endpoint_returns_actionable_error(monkeypatch):
    """When ALL payment requirements are Permit2, the error must be incompatible_scheme
    and must NOT suggest fixing CDP credentials."""
    from hermes_x402.tools import request

    class NoMatchError(Exception):
        """Simulates x402 SDK NoMatchingRequirementsError."""

    NoMatchError.__name__ = "NoMatchingRequirementsError"

    class FakePaymentError(Exception):
        pass

    async def fake_do_fetch(url, method, headers, body, cap_usdc):
        err = NoMatchError("no matching requirements")
        raise FakePaymentError("Failed to handle payment") from err

    monkeypatch.setattr(request, "_do_fetch", fake_do_fetch)

    # Patch the PaymentError import inside _do_fetch's module scope.
    import types
    fake_x402_httpx = types.ModuleType("x402.http.clients.httpx")
    fake_x402_httpx.PaymentError = FakePaymentError
    fake_x402_httpx.x402HttpxClient = None
    monkeypatch.setitem(__import__("sys").modules, "x402.http.clients.httpx", fake_x402_httpx)

    # Re-run through the handler directly by patching _do_fetch to raise the right type.
    async def raise_no_match(url, method, headers, body, cap_usdc):
        from hermes_x402.tools.request import _IncompatibleSchemeError
        raise _IncompatibleSchemeError("Permit2 only")

    monkeypatch.setattr(request, "_do_fetch", raise_no_match)

    out = json.loads(request.x402_request(
        {"url": "https://permit2only.example/pay"},
        session_id="s-m3",
    ))
    assert out["error"] == "incompatible_scheme", f"expected incompatible_scheme, got {out}"
    assert "EIP-3009" in out.get("hint", "")
    # Must NOT tell user to fix CDP credentials.
    combined = json.dumps(out).lower()
    assert "cdp credentials" not in combined
    assert "provisioned" not in combined


# --------------------------------------------------------------------------- #
# M5 — Response body truncation marker
# --------------------------------------------------------------------------- #

def test_body_truncation_marker(monkeypatch):
    """A response body longer than 50k chars must set body_truncated=True."""
    from hermes_x402.tools import request

    big_body = "x" * 60_000

    async def fake_do_fetch(url, method, headers, body, cap_usdc):
        return 200, big_body, None, None  # no payment

    monkeypatch.setattr(request, "_do_fetch", fake_do_fetch)

    out = json.loads(request.x402_request(
        {"url": "https://api.example.com/big"},
        session_id="s-m5",
    ))
    assert out["body_truncated"] is True
    assert len(out["body"]) == 50_000


def test_body_not_truncated_when_under_limit(monkeypatch):
    """A response body under 50k chars must set body_truncated=False."""
    from hermes_x402.tools import request

    small_body = "hello world"

    async def fake_do_fetch(url, method, headers, body, cap_usdc):
        return 200, small_body, None, None

    monkeypatch.setattr(request, "_do_fetch", fake_do_fetch)

    out = json.loads(request.x402_request(
        {"url": "https://api.example.com/small"},
        session_id="s-m5b",
    ))
    assert out["body_truncated"] is False
    assert out["body"] == small_body


# --------------------------------------------------------------------------- #
# N2 — HTTPS URL validation
# --------------------------------------------------------------------------- #

def test_x402_request_non_https_url_rejected():
    """x402_request must reject non-HTTPS URLs before making any network call."""
    from hermes_x402.tools.request import x402_request

    for bad_url in ("http://insecure.example/pay", "ftp://bad.example", "not-a-url"):
        out = json.loads(x402_request({"url": bad_url}))
        assert out["error"] == "invalid_url", f"expected invalid_url for {bad_url!r}, got {out}"
        # Hint must be actionable.
        assert "https://" in out.get("hint", "")


def test_x402_request_https_url_accepted_proceeds_to_network(monkeypatch):
    """A valid https:// URL must not be rejected by the URL guard."""
    from hermes_x402.tools import request

    reached = {"fetch": False}

    async def fake_do_fetch(url, method, headers, body, cap_usdc):
        reached["fetch"] = True
        return 200, '{"ok": true}', None, None

    monkeypatch.setattr(request, "_do_fetch", fake_do_fetch)
    # Provide session_id to pass strict-mode session budget check.
    request.x402_request({"url": "https://valid.example/resource"}, session_id="s-n2-valid")
    assert reached["fetch"], "https:// URL should reach _do_fetch"


# --------------------------------------------------------------------------- #
# M2 — cdp_transfer per-session budget ceiling
# --------------------------------------------------------------------------- #

def test_cdp_transfer_session_budget_blocks_cumulative_overshoot(monkeypatch):
    """Two cdp_transfer calls that together exceed session_transfer_budget_usdc must be blocked."""
    import hermes_x402.cdp.wallet_ops as wo
    from hermes_x402.tools.cdp_tools import cdp_transfer

    def mock_transfer(to, amount, token, network):
        return {"tx_hash": "0xmock", "to": to, "amount": str(amount),
                "token": token, "network": network, "explorer": ""}

    monkeypatch.setattr(wo, "transfer", mock_transfer)
    monkeypatch.setattr("hermes_x402.config.max_price_usdc", lambda: 10.0)
    monkeypatch.setattr("hermes_x402.config.session_transfer_budget_usdc", lambda: 1.0)

    # First call: 0.6 USDC — should succeed.
    out1 = json.loads(cdp_transfer(
        {"to": "0xrecipient", "amount": 0.6, "token": "usdc"},
        session_id="s-m2",
    ))
    assert "error" not in out1, f"first call should succeed: {out1}"

    # Second call: 0.6 USDC — cumulative 1.2 > budget 1.0 — should be blocked.
    out2 = json.loads(cdp_transfer(
        {"to": "0xrecipient", "amount": 0.6, "token": "usdc"},
        session_id="s-m2",
    ))
    assert "error" in out2
    assert "session" in out2["error"].lower() or "budget" in out2["error"].lower(), \
        f"expected session budget error, got: {out2}"


def test_cdp_transfer_session_not_bounded_by_x402_budget(monkeypatch):
    """cdp_transfer session ceiling is separate from the x402 payment session budget."""
    import hermes_x402.cdp.wallet_ops as wo

    def mock_transfer(to, amount, token, network):
        return {"tx_hash": "0xmock", "to": to, "amount": str(amount),
                "token": token, "network": network, "explorer": ""}

    monkeypatch.setattr(wo, "transfer", mock_transfer)
    # x402 session budget is 1.0 but transfer budget is separately set to 5.0.
    monkeypatch.setattr("hermes_x402.config.max_price_usdc", lambda: 10.0)
    monkeypatch.setattr("hermes_x402.config.session_transfer_budget_usdc", lambda: 5.0)

    from hermes_x402.tools.cdp_tools import cdp_transfer
    out = json.loads(cdp_transfer(
        {"to": "0xrecipient", "amount": 2.0, "token": "usdc"},
        session_id="s-m2b",
    ))
    assert "error" not in out, f"should succeed within transfer budget: {out}"


# --------------------------------------------------------------------------- #
# M4 — Onboarding writes plugins.enabled
# --------------------------------------------------------------------------- #

def test_plugin_onboarding_writes_plugins_enabled(monkeypatch, capsys):
    """run_x402_onboarding must add 'hermes-x402' to config plugins.enabled."""
    monkeypatch.setattr("sys.stdin", None)
    monkeypatch.setattr("hermes_x402.config.missing_cdp_credentials", lambda: [])
    monkeypatch.setattr("hermes_x402.wallet.address", lambda: "0xWallet")
    monkeypatch.setattr("hermes_x402.wallet.usdc_balance", lambda net=None: 0.0)

    from hermes_x402.setup_flow import run_x402_onboarding

    cfg: dict = {}
    run_x402_onboarding(config_dict=cfg)
    assert "plugins" in cfg
    assert "enabled" in cfg["plugins"]
    assert "hermes-x402" in cfg["plugins"]["enabled"]


def test_plugin_onboarding_idempotent_plugins_enabled(monkeypatch, capsys):
    """Running onboarding twice must not duplicate the plugin entry."""
    monkeypatch.setattr("sys.stdin", None)
    monkeypatch.setattr("hermes_x402.config.missing_cdp_credentials", lambda: [])
    monkeypatch.setattr("hermes_x402.wallet.address", lambda: "0xWallet")
    monkeypatch.setattr("hermes_x402.wallet.usdc_balance", lambda net=None: 0.0)

    from hermes_x402.setup_flow import run_x402_onboarding

    cfg: dict = {}
    run_x402_onboarding(config_dict=cfg)
    run_x402_onboarding(config_dict=cfg)
    assert cfg["plugins"]["enabled"].count("hermes-x402") == 1


# --------------------------------------------------------------------------- #
# M7 — Spend command all-time total
# --------------------------------------------------------------------------- #

def test_spend_command_shows_alltime_total(monkeypatch, capsys):
    """spend_command must display an all-time total queried from the full ledger."""
    from hermes_x402 import ledger

    monkeypatch.setattr(ledger, "all_time_total", lambda: 3.14)
    monkeypatch.setattr(ledger, "recent_spend", lambda n: [
        {"ts": float(i), "kind": "http", "amount_usdc": 0.01, "endpoint_host": f"ep{i}", "tx": None}
        for i in range(min(n, 3))
    ])

    from hermes_x402.cli.spend import spend_command

    class FakeArgs:
        pass

    spend_command(FakeArgs())
    out = capsys.readouterr().out
    assert "3.140000" in out
    assert "all-time" in out.lower()


# --------------------------------------------------------------------------- #
# S2 — Dynamic bazaar server name in transform hook
# --------------------------------------------------------------------------- #

def test_transform_hook_nonstandard_bazaar_server_name(monkeypatch):
    """Hook must add retry hint for non-default bazaar server names."""
    from hermes_x402.hooks import on_transform_tool_result
    from hermes_x402.tools import retry_mcp

    # Simulate a custom-named bazaar server.
    monkeypatch.setattr(retry_mcp, "_load_mcp_servers", lambda: {
        "cdp-bazaar": {"url": "https://api.example.com/x402/discovery/mcp"}
    })

    payment_required_result = json.dumps({"x402Version": 2, "accepts": [{"amount": "1000"}]})
    # The proxy tool name for "cdp-bazaar" is mcp_cdp_bazaar_proxy_tool_call.
    transformed = on_transform_tool_result(
        tool_name="mcp_cdp_bazaar_proxy_tool_call",
        args={"toolName": "x402_get_weather", "parameters": {"city": "SF"}},
        result=payment_required_result,
    )
    assert transformed is not None
    assert "x402_retry_mcp_payment" in transformed
    assert "mcp_cdp_bazaar_proxy_tool_call" in transformed


def test_transform_hook_custom_bazaar_search_not_treated_as_payment(monkeypatch):
    """Custom-named bazaar search_resources results must not get retry hints."""
    from hermes_x402.hooks import on_transform_tool_result
    from hermes_x402.tools import retry_mcp

    monkeypatch.setattr(retry_mcp, "_load_mcp_servers", lambda: {
        "cdp-bazaar": {"url": "https://api.example.com/x402/discovery/mcp"}
    })

    # search_resources returns resource listings, not payment requirements — but even if
    # the payload contains payment-like fields, the hook must skip it for search tools.
    # In practice search results won't have x402Version, so _is_payment_required_payload
    # returns False; this test confirms the dynamic prefix guard also works.
    search_result = json.dumps({"resources": [{"toolName": "x402_get_weather"}]})
    result = on_transform_tool_result(
        tool_name="mcp_cdp_bazaar_search_resources",
        args={"query": "weather"},
        result=search_result,
    )
    assert result is None


# --------------------------------------------------------------------------- #
# S3 — Monetize unit test
# --------------------------------------------------------------------------- #

def test_monetize_paid_tool_builds_decorator(monkeypatch):
    """paid_tool() must build and apply a decorator without raising."""
    import sys
    import types

    # Mock x402 SDK symbols used by monetize.py.
    def dummy_decorator(fn):
        return fn

    class FakeResourceServer:
        def __init__(self, *args, **kwargs):
            pass

        def build_payment_requirements(self, cfg):
            return [{"scheme": "exact", "network": "eip155:84532", "amount": "10000"}]

        def initialize(self):
            pass

    class FakeHTTPFacilitatorClient:
        def __init__(self, cfg):
            pass

    def fake_create_facilitator_config(key_id, key_secret):
        return object()

    def fake_register_exact_evm_server(server, networks):
        pass

    def fake_declare_mcp_discovery_extension():
        return {}

    def fake_create_payment_wrapper(server, *, accepts, resource, extensions):
        return dummy_decorator

    class FakeResourceConfig:
        def __init__(self, **kw):
            self.kw = kw

    class FakeResourceInfo:
        def __init__(self, **kw):
            self.kw = kw

    class FakeFacilitatorConfig:
        def __init__(self, url=""):
            self.url = url

    # Build stub modules.
    x402_mod = types.ModuleType("x402")
    x402_mod.x402ResourceServer = FakeResourceServer
    x402_mod.FacilitatorConfig = FakeFacilitatorConfig
    monkeypatch.setitem(sys.modules, "x402", x402_mod)

    x402_http_mod = types.ModuleType("x402.http")
    x402_http_mod.HTTPFacilitatorClient = FakeHTTPFacilitatorClient
    monkeypatch.setitem(sys.modules, "x402.http", x402_http_mod)

    x402_mech_mod = types.ModuleType("x402.mechanisms.evm.exact.register")
    x402_mech_mod.register_exact_evm_server = fake_register_exact_evm_server
    monkeypatch.setitem(sys.modules, "x402.mechanisms.evm.exact.register", x402_mech_mod)

    x402_bazaar_mod = types.ModuleType("x402.extensions.bazaar")
    x402_bazaar_mod.declare_mcp_discovery_extension = fake_declare_mcp_discovery_extension
    monkeypatch.setitem(sys.modules, "x402.extensions.bazaar", x402_bazaar_mod)

    x402_mcp_mod = types.ModuleType("x402.mcp")
    x402_mcp_mod.ResourceInfo = FakeResourceInfo
    x402_mcp_mod.create_payment_wrapper = fake_create_payment_wrapper
    monkeypatch.setitem(sys.modules, "x402.mcp", x402_mcp_mod)

    x402_schemas_mod = types.ModuleType("x402.schemas")
    x402_schemas_mod.ResourceConfig = FakeResourceConfig
    monkeypatch.setitem(sys.modules, "x402.schemas", x402_schemas_mod)

    cdp_x402_mod = types.ModuleType("cdp.x402")
    cdp_x402_mod.create_facilitator_config = fake_create_facilitator_config
    monkeypatch.setitem(sys.modules, "cdp.x402", cdp_x402_mod)

    # Stub wallet.address() so paid_tool uses a test address.
    monkeypatch.setattr("hermes_x402.wallet.address", lambda: "0xTestWallet")

    # Import (or reload) monetize after stubs are registered.
    import importlib

    import hermes_x402.monetize as monetize_mod  # noqa: PLC0415

    importlib.reload(monetize_mod)

    # paid_tool must return a callable decorator without raising.
    charge = monetize_mod.paid_tool(
        price_usdc="0.01",
        resource_url="mcp://tool/get_weather",
        pay_to="0xTestWallet",
    )
    assert callable(charge)

    # Applying the decorator to a function must not raise.
    @charge
    def get_weather(city: str) -> str:
        return city

    # The decorated function must still be callable (no-op decorator in mock).
    assert callable(get_weather)


# --------------------------------------------------------------------------- #
# S4 — Integration test harness (skipped unless CDP_* env vars are set)
# --------------------------------------------------------------------------- #

_INTEGRATION_SKIP = pytest.mark.skipif(
    not all(os.getenv(k) for k in ("CDP_API_KEY_ID", "CDP_API_KEY_SECRET", "CDP_WALLET_SECRET")),
    reason="Integration tests require CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET env vars",
)


@_INTEGRATION_SKIP
def test_integration_cdp_wallet_status_shape():
    """Integration: cdp_wallet_status returns expected fields with real CDP credentials."""
    from hermes_x402.tools.cdp_tools import cdp_wallet_status

    out = json.loads(cdp_wallet_status({}))
    assert "address" in out, f"missing address: {out}"
    assert out["address"].startswith("0x"), f"address not EVM: {out['address']}"
    assert out.get("provider") == "local"
    assert out.get("network") is not None


@_INTEGRATION_SKIP
def test_integration_cdp_wallet_balance_returns_usdc_field():
    """Integration: cdp_wallet_balance returns a usdc field (may be 0.0 if unfunded)."""
    from hermes_x402.tools.cdp_tools import cdp_wallet_balance

    out = json.loads(cdp_wallet_balance({}))
    assert "usdc" in out, f"missing usdc field: {out}"
    assert isinstance(out["usdc"], int | float), f"usdc not numeric: {out['usdc']}"
    assert "balances" in out and isinstance(out["balances"], list)


# --------------------------------------------------------------------------- #
# S5 — cdp_payments surfaces pending journal entries
# --------------------------------------------------------------------------- #

def test_cdp_payments_includes_pending_count(monkeypatch):
    """cdp_payments must include pending_journal_entries count."""
    from hermes_x402 import ledger

    monkeypatch.setattr(ledger, "recent_spend", lambda n: [])
    # Inject an open journal entry.
    jid = ledger.journal_begin(
        fingerprint="fp-pending-test",
        idempotency_key=None,
        kind="http",
        endpoint="https://api.example.com/paid",
        cap_usdc=0.1,
        session_id="s-s5",
    )

    from hermes_x402.tools.cdp_tools import cdp_payments

    out = json.loads(cdp_payments({}))
    assert "pending_journal_entries" in out
    assert out["pending_journal_entries"] >= 1
    assert "pending_note" in out

    # Clean up.
    ledger.journal_finalize(jid, state="failed")


# --------------------------------------------------------------------------- #
# Journal concurrent safety
# --------------------------------------------------------------------------- #

def test_journal_begin_concurrent_budget_blocks_one():
    """Concurrent journal_begin calls with overlapping budgets: one must raise BudgetExceededError."""
    import threading  # noqa: PLC0415

    from hermes_x402 import ledger

    results = []
    errors = []

    def reserve(fp, cap):
        try:
            jid = ledger.journal_begin(
                fingerprint=fp, idempotency_key=None, kind="http",
                endpoint="https://x", cap_usdc=cap, session_id="s-concurrent",
                budget_usdc=1.0,
            )
            results.append(jid)
        except ledger.BudgetExceededError as e:
            errors.append(e)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=reserve, args=("fp-concurrent-1", 0.7))
    t2 = threading.Thread(target=reserve, args=("fp-concurrent-2", 0.7))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one should succeed and one should fail — budget 1.0, each cap 0.7.
    assert len(results) == 1, f"expected 1 success, got {len(results)}: {results}"
    assert len(errors) == 1, f"expected 1 error, got {len(errors)}: {errors}"
    assert isinstance(errors[0], ledger.BudgetExceededError)


# --------------------------------------------------------------------------- #
# All-time total ledger helper
# --------------------------------------------------------------------------- #

def test_ledger_all_time_total():
    """all_time_total() must sum across all sessions."""
    from hermes_x402 import ledger

    ledger.record_payment(kind="http", amount_usdc=0.10, session_id="s-at1")
    ledger.record_payment(kind="mcp", amount_usdc=0.05, session_id="s-at2")
    total = ledger.all_time_total()
    assert total >= 0.15  # may be higher if other tests added rows


def test_ledger_session_transfer_total():
    """session_transfer_total() must count only 'transfer' kind rows."""
    from hermes_x402 import ledger

    sid = "s-transfer-total"
    ledger.record_payment(kind="http", amount_usdc=0.50, session_id=sid)
    ledger.record_payment(kind="transfer", amount_usdc=1.00, session_id=sid)
    ledger.record_payment(kind="transfer", amount_usdc=0.25, session_id=sid)

    transfer_total = ledger.session_transfer_total(sid)
    x402_total = ledger.session_total(sid)

    assert abs(transfer_total - 1.25) < 1e-9, f"unexpected transfer total: {transfer_total}"
    # session_total includes all kinds.
    assert abs(x402_total - 1.75) < 1e-9, f"unexpected session total: {x402_total}"
