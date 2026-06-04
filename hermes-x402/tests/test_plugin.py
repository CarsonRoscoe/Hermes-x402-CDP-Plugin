"""Unit tests for the hermes-x402 plugin. No network; the Coinbase MCP is mocked."""

from __future__ import annotations

import json

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
    assert ctx.tools == ["x402_request", "x402_retry_mcp_payment"]
    assert ctx.cli == ["x402"]
    assert ctx.cmds == ["x402"]
    assert ctx.skills == ["x402-payments"]
    assert set(ctx.hooks) == {"pre_tool_call", "on_session_end"}


# --------------------------------------------------------------------------- #
# tools validate args / return JSON
# --------------------------------------------------------------------------- #
def test_request_returns_json_on_bad_args():
    from hermes_x402.tools.request import x402_request

    assert json.loads(x402_request({}))["error"]


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


def test_retry_errors_when_server_has_no_url(monkeypatch):
    from hermes_x402.tools.retry_mcp import x402_retry_mcp_payment

    _mock_servers(monkeypatch, {"local": {"command": "some-stdio-server"}})
    out = json.loads(x402_retry_mcp_payment({"tool_name": "mcp_local_pay", "arguments": {}}))
    assert "url" in out["error"]


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


def test_payment_client_delegates_to_connection(monkeypatch):
    import asyncio

    pytest.importorskip("x402.schemas", reason="x402 v2 SDK not installed")

    from hermes_x402.coinbase_mcp.payment_client import CoinbaseMcpPaymentClient

    payment_required = {
        "x402Version": 2,
        "resource": {"url": "https://x/y"},
        "accepts": [{"amount": "10000", "network": "eip155:8453"}],
    }
    signed = {
        "x402Version": 2,
        "resource": {"url": "https://x/y"},
        "accepted": {"scheme": "exact", "network": "eip155:8453", "amount": "10000",
                     "asset": "0x0", "payTo": "0x0", "resource": "https://x/y"},
        "payload": {"signature": "0xabc", "authorization": {}},
    }
    calls = {}

    class _Conn:
        async def call_tool(self, name, args):
            calls["name"] = name
            calls["args"] = args
            return {"payment_payload": signed}

    monkeypatch.setattr(
        "x402.schemas.PaymentPayload.model_validate", staticmethod(lambda d: d)
    )
    client = CoinbaseMcpPaymentClient(_Conn(), max_price_usdc=1.0)
    out = asyncio.run(client.create_payment_payload(payment_required))
    assert calls["name"] == "create_payment_payload"
    assert "payment_required" in calls["args"]
    assert out == signed
    assert client.last_min_usdc == pytest.approx(0.01)


# --------------------------------------------------------------------------- #
# mcp_servers registration
# --------------------------------------------------------------------------- #
def test_ensure_mcp_servers_writes_both():
    from hermes_x402 import mcp_servers

    cfg: dict = {}
    names = mcp_servers.ensure_mcp_servers(cfg)
    assert names == ["coinbase", "bazaar"]
    servers = cfg["mcp_servers"]
    # stdio default for coinbase
    assert servers["coinbase"]["command"] == "fake-coinbase-mcp"
    assert servers["bazaar"]["url"]


def test_ensure_mcp_servers_sources_from_passed_config():
    from hermes_x402 import mcp_servers

    cfg = {
        "x402": {
            "coinbase_mcp": {
                "transport": "remote",
                "url": "https://signer.example/mcp",
                "auth_token_env": "MY_TOKEN",
            },
            "bazaar_mcp": {"url": "https://bazaar.example/custom"},
        }
    }
    mcp_servers.ensure_mcp_servers(cfg)
    servers = cfg["mcp_servers"]
    # Mirror reflects the in-memory config, not disk defaults.
    assert servers["coinbase"]["url"] == "https://signer.example/mcp"
    assert servers["coinbase"]["headers"] == {"Authorization": "Bearer ${MY_TOKEN}"}
    assert servers["bazaar"]["url"] == "https://bazaar.example/custom"


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
    res = budget.pre_tool_call(tool_name="x402_request", args={}, task_id="s1")
    assert res and res["action"] == "block"
    assert budget.pre_tool_call(tool_name="x402_retry_mcp_payment", args={}, task_id="s1")["action"] == "block"
    assert budget.pre_tool_call(tool_name="read_file", args={}, task_id="s1") is None


def test_budget_hook_blocks_projected_overshoot(monkeypatch):
    from hermes_x402 import budget

    # Under budget so far, but the pending call's cap would push past it.
    monkeypatch.setattr(budget.config, "session_budget_usdc", lambda: 1.0)
    monkeypatch.setattr(budget.config, "max_price_usdc", lambda: 0.0)
    monkeypatch.setattr(budget.ledger, "session_total", lambda sid: 0.8)
    blocked = budget.pre_tool_call(
        tool_name="x402_request", args={"max_price_usdc": 0.5}, task_id="s1"
    )
    assert blocked and blocked["action"] == "block"
    # A small cap that stays within budget is allowed.
    assert budget.pre_tool_call(
        tool_name="x402_request", args={"max_price_usdc": 0.1}, task_id="s1"
    ) is None


def test_config_coinbase_mcp_defaults(tmp_path):
    from hermes_x402 import config

    assert str(tmp_path) in str(config.data_dir())
    cmcp = config.coinbase_mcp_config()
    assert cmcp["transport"] == "stdio"
    # command resolves to the venv-local binary (full path) when available, or the bare
    # name for PATH-based fallback — either way it must contain "fake-coinbase-mcp".
    assert "fake-coinbase-mcp" in cmcp["command"]
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


def test_r4_selected_amount_over_cap_rejected(monkeypatch):
    # Signer selects a 5 USDC option although the cheapest accept (0.1) fit the 1.0 cap.
    import asyncio

    from hermes_x402.coinbase_mcp.payment_client import (
        CoinbaseMcpPaymentClient,
        PaymentExceedsCapError,
    )

    monkeypatch.setattr("x402.schemas.PaymentPayload.model_validate", staticmethod(lambda d: d))
    pr = {"x402Version": 2, "accepts": [{"amount": "100000"}]}

    class Conn:
        async def call_tool(self, name, args):
            return {"payment_payload": {"accepted": {"amount": "5000000"}}}

    client = CoinbaseMcpPaymentClient(Conn(), max_price_usdc=1.0)
    with pytest.raises(PaymentExceedsCapError):
        asyncio.run(client.create_payment_payload(pr))


def test_r4_unverifiable_selected_amount_rejected_strict(monkeypatch):
    import asyncio

    from hermes_x402.coinbase_mcp.payment_client import (
        CoinbaseMcpPaymentClient,
        PaymentVerificationError,
    )

    monkeypatch.setattr("x402.schemas.PaymentPayload.model_validate", staticmethod(lambda d: d))
    monkeypatch.setattr("hermes_x402.config.is_strict", lambda: True)
    pr = {"x402Version": 2, "accepts": [{"amount": "100000"}]}

    class Conn:
        async def call_tool(self, name, args):
            return {"payment_payload": {"accepted": {}}}  # no selected amount

    client = CoinbaseMcpPaymentClient(Conn(), max_price_usdc=1.0)
    with pytest.raises(PaymentVerificationError):
        asyncio.run(client.create_payment_payload(pr))


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
    res = budget.pre_tool_call(tool_name="x402_request", args={}, task_id="s1")
    assert res and res["action"] == "block"

    monkeypatch.setattr(budget.config, "is_strict", lambda: False)
    assert budget.pre_tool_call(tool_name="x402_request", args={}, task_id="s1") is None


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
