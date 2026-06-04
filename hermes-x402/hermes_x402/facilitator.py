"""CDP Facilitator wiring for the monetize (server) side.

The CDP facilitator (``https://api.cdp.coinbase.com/platform/v2/x402``) supports all
networks and is auth'd with CDP API keys. ``cdp.x402.create_facilitator_config`` builds
the authed config (Ed25519/JWT headers) from the env credentials. Without CDP creds we
fall back to the signup-free testnet facilitator (``https://x402.org/facilitator``).

Note: client-side *discovery* no longer uses this — it goes through the Bazaar MCP
(``bazaar_mcp``). This module is only used by ``monetize`` for resource-server
verify/settle.
"""

from __future__ import annotations

import logging
import os

from . import config

logger = logging.getLogger(__name__)

TESTNET_FACILITATOR_URL = "https://x402.org/facilitator"
_CDP_ENV = ("CDP_API_KEY_ID", "CDP_API_KEY_SECRET")


def _has_cdp_creds() -> bool:
    return all(os.getenv(k) for k in _CDP_ENV)


def facilitator_config():
    """Build a facilitator config: CDP (authed) when creds exist, else testnet."""
    if _has_cdp_creds():
        try:
            from cdp.x402 import create_facilitator_config

            return create_facilitator_config(
                os.environ["CDP_API_KEY_ID"], os.environ["CDP_API_KEY_SECRET"]
            )
        except Exception as exc:
            logger.warning("hermes-x402: CDP facilitator config failed (%s); using testnet", exc)

    from x402 import FacilitatorConfig

    url = config.CDP_FACILITATOR_URL if _has_cdp_creds() else TESTNET_FACILITATOR_URL
    return FacilitatorConfig(url=url)
