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


# --------------------------------------------------------------------------- #
# mcp_servers registration
# --------------------------------------------------------------------------- #
def test_ensure_mcp_servers_local_provider_bazaar_only():
    """Local provider (default): only bazaar is registered; no coinbase MCP subprocess."""
    from hermes_x402 import mcp_servers

    cfg: dict = {"x402": {"provider": "local"}}
    names = mcp_servers.ensure_mcp_servers(cfg)
    assert names == ["bazaar"]
    servers = cfg["mcp_servers"]
    assert "coinbase" not in servers
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


def test_onboarding_remote_choice_falls_back_to_local(monkeypatch, capsys):
    """Selecting option 2 (Coming Soon) silently falls back to local."""
    import io
    fake_stdin = io.StringIO("2\n")
    fake_stdin.isatty = lambda: True  # pretend it's a real TTY so the menu is shown
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("hermes_x402.config.missing_cdp_credentials", lambda: [])
    monkeypatch.setattr("hermes_x402.wallet.address", lambda: "0xLocalWallet")
    monkeypatch.setattr("hermes_x402.wallet.usdc_balance", lambda net=None: 0.0)

    from hermes_x402.setup_flow import run_x402_onboarding

    summary = run_x402_onboarding(config_dict={})
    assert summary["provider"] == "local"
    out = capsys.readouterr().out
    assert "Coming Soon" in out


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
    assert set(out) == {"payments", "count", "total_usdc"}
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
    assert normalize_provider("  coinbase_mcp  ") == "coinbase_mcp"
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
