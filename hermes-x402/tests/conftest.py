"""Test fixtures: isolate HERMES_HOME and reset module caches per test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    # Reset the cached Coinbase MCP connection so each test starts clean.
    import hermes_x402.coinbase_mcp.connection as conn

    conn._cached = None
    yield
