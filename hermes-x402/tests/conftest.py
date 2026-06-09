"""Test fixtures: isolate HERMES_HOME and reset module-level singletons per test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    # Reset the CDP Wallet singleton so cached signer/address don't bleed between tests.
    import hermes_x402.cdp.client as cdp_client

    cdp_client.wallet._signer = None
    cdp_client.wallet._address = None
    cdp_client.wallet._account_name = None
    cdp_client.wallet._account = None
    cdp_client.wallet._lock = __import__("asyncio").Lock()
    yield
