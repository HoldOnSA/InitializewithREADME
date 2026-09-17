"""Render one feed line as HTML.

Server-side so that `/feed`, the token dashboard and the WebSocket stream all
produce byte-identical markup from one implementation.
"""

from __future__ import annotations

from typing import Any, Dict

from markupsafe import Markup, escape

from .format import bps, sol, tokens


def _wallet(key: Any) -> str:
    if not key:
        return '<span class="dim">-</span>'
    safe = escape(str(key))
    label = escape(f"{str(key)[:4]}…{str(key)[-4:]}") if len(str(key)) > 9 else safe
    return f'<a href="/passport/{safe}" class="mono">{label}</a>'


def render_event_html(event: Dict[str, Any], decimals: int = 6) -> Markup:
    """Mirrors the wording used across the UI. Returns escaped, safe markup."""
    name = event.get("name", "")
    d = event.get("data", {}) or {}

    def tok(value: Any) -> str:
        return escape(tokens(value, decimals))

    if name == "BuyExecuted":
        html = (
            f'{_wallet(d.get("buyer"))} bought '
            f'<b class="mono" style="color:var(--stack)">{tok(d.get("amount"))}</b> '
            f'for {escape(sol(d.get("cost_lamports")))}'
        )

    elif name == "ExitExecuted":
        kind = escape(str(d.get("kind_name") or "sold"))
        html = f'{_wallet(d.get("owner"))} {kind} <b class="mono">{tok(d.get("gross"))}</b>'
        if d.get("tax"):
            html += (
                f' · <span style="color:var(--tax)">{tok(d.get("tax"))} tax</span> '
                f'at {escape(bps(d.get("top_tax_bps", 0)))}'
            )
        else:
            html += ' · <span style="color:var(--stack)">no tax, fully matured</span>'
        if d.get("kind_name") == "transfer" and d.get("destination"):
            html += f' → {_wallet(d.get("destination"))}'

    elif name == "TaxCollected":
        suffix = " (buffered — no eligible holders yet)" if d.get("undistributed") else ""
        html = (
            f'<span style="color:var(--tax)">{tok(d.get("amount"))} routed to the '
            f'loyalty pool{escape(suffix)}</span>'
        )

    elif name == "PoolClaimed":
        html = (
            f'{_wallet(d.get("owner"))} claimed '
            f'<b class="mono" style="color:var(--pool)">{tok(d.get("amount"))}</b> from the pool'
        )

    elif name == "VestedClaimed":
        html = f'{_wallet(d.get("owner"))} unlocked <span class="mono">{tok(d.get("released"))}</span>'

    elif name == "TierUp":
        html = (
            f'<span style="color:var(--tier)">{_wallet(d.get("owner"))} reached tier '
            f'{escape(str(d.get("new_tier")))} (from {escape(str(d.get("previous_tier")))})</span>'
        )

    elif name == "ReputationUpdated":
        delta = f'{int(d.get("score_delta", 0)):,}'
        html = (
            f'{_wallet(d.get("owner"))} credited '
            f'<span class="mono" style="color:var(--tier)">{escape(delta)}</span> reputation '
            f'for {escape(sol(d.get("capital_at_risk_lamports")))} held'
        )

    elif name == "LaunchInitialized":
        html = (
            f'{_wallet(d.get("creator"))} launched a token · opening tax '
            f'<span style="color:var(--tax)">{escape(bps(d.get("opening_tax_bps", 0)))}</span>'
        )

    elif name == "WeightSynced":
        html = (
            f'<span class="dim">tenure weight synced '
            f'{int(d.get("previous_weight", 0)):,} → {int(d.get("new_weight", 0)):,}</span>'
        )

    elif name == "LotsCompacted":
        html = (
            f'<span class="dim">{_wallet(d.get("owner"))} compacted lots '
            f'({escape(str(d.get("lots_remaining")))} remaining)</span>'
        )

    else:
        html = f'<span class="dim">{escape(name)}</span>'

    return Markup(html)
