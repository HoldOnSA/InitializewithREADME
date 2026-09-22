"""Render one feed line as HTML.

Server-side so that `/feed`, the token dashboard and the WebSocket stream all
produce byte-identical markup from one implementation.
"""

from __future__ import annotations

from typing import Any, Dict

from markupsafe import Markup, escape

from .format import sol


def _wallet(key: Any) -> str:
    if not key:
        return '<span class="dim">-</span>'
    text = str(key)
    label = escape(f"{text[:4]}…{text[-4:]}") if len(text) > 9 else escape(text)
    return f'<span class="mono">{label}</span>'


def render_event_html(event: Dict[str, Any], decimals: int = 0) -> Markup:
    """Mirrors the wording used across the UI. Returns escaped, safe markup."""
    name = event.get("name", "")
    d = event.get("data", {}) or {}

    if name == "MintRegistered":
        html = (
            f'{_wallet(d.get("creator"))}\'s token was registered for the loyalty '
            f'layer'
        )

    elif name == "WalletRegistered":
        html = f'{_wallet(d.get("owner"))} registered'

    elif name == "WeightSynced":
        html = (
            f'<span class="dim">{_wallet(d.get("owner"))} weight synced '
            f'{int(d.get("previous_weight", 0)):,} → {int(d.get("new_weight", 0)):,}</span>'
        )

    elif name == "FeeCollected":
        suffix = " (buffered — no eligible holders yet)" if d.get("undistributed") else ""
        html = (
            f'<span style="color:var(--pool)">{escape(sol(d.get("amount")))} donated to the '
            f'loyalty pool{escape(suffix)}</span>'
        )

    elif name == "RewardClaimed":
        html = (
            f'{_wallet(d.get("owner"))} claimed '
            f'<b class="mono" style="color:var(--pool)">{escape(sol(d.get("amount")))}</b>'
        )

    elif name == "VaultDeficitDetected":
        # Should never happen under correct operation - see reconcile.rs.
        # Always shown, even though the reconcile() call that emitted it
        # always fails - that's the whole point of this event.
        html = (
            f'<span style="color:var(--tax)"><strong>Vault deficit detected</strong> - real '
            f'balance {escape(sol(d.get("actual_lamports")))} is '
            f'{escape(sol(d.get("deficit")))} under what the vault\'s own bookkeeping expects '
            f'({escape(sol(d.get("expected_lamports")))}). This should never happen - '
            f'investigate.</span>'
        )

    else:
        html = f'<span class="dim">{escape(name)}</span>'

    return Markup(html)
