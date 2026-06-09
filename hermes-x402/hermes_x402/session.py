"""Session identity helpers for money guards.

The x402 session budget is scoped to the *conversation session* (Hermes session_id),
not the per-turn task_id. Hermes exposes this as ``session_id`` hook kwargs and as the
``HERMES_SESSION_ID`` environment variable during tool execution.
"""

from __future__ import annotations

import os


def current_session_id(kwargs: dict | None = None) -> str | None:
    """Return the active Hermes conversation session id, if available."""
    data = kwargs or {}
    sid = data.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    env_sid = os.environ.get("HERMES_SESSION_ID")
    if isinstance(env_sid, str) and env_sid.strip():
        return env_sid.strip()
    return None
