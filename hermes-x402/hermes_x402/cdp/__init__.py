"""Local CDP server-wallet core (self-custodial).

This package is the in-plugin implementation of the ``local`` wallet provider: it talks to
the CDP SDK directly to provision a CDP server wallet and run wallet management + x402
signing, with no Coinbase MCP subprocess. It is the single source of truth for the CDP
logic shared by the native ``cdp_*`` agent tools, the ``hermes x402`` CLI reads, and the
x402 payment signer.

- :mod:`.client` — the ``Wallet`` singleton (account provisioning, async CDP operations).
- :mod:`.wallet_ops` — synchronous wrappers used by the tools and CLI.
- :mod:`.signer` — x402 ``create_payment_payload`` backed by the CDP wallet.

CDP and x402 SDK imports are intentionally lazy (inside functions) so importing this
package never requires those dependencies until the local provider is actually used.
"""

from __future__ import annotations

__all__ = ["client", "wallet_ops", "signer"]
