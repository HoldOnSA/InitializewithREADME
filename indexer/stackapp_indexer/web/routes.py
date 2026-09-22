"""The Python-served UI.

Server-rendered Jinja pages plus a thin JSON layer for the wallet flow. No
bundler, no Node, no npm - the whole prototype runs on Python.

The wallet flow is:

    browser  POST /api/tx/<action> {wallet, ...}
    server   builds an UNSIGNED transaction message with txbuild.py
    browser  window.solana.request({method:"signAndSendTransaction", ...})

The server never sees a private key or a signature - including for
`register_mint` and `write_registration`, which are authority-gated on chain
but built and signed exactly like every other action here: whichever wallet
the operator connects. If that wallet isn't the real
`GlobalConfig.authority`, the transaction just fails on chain.
"""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stackapp_sim.constants import TENURE_TIER_MULTIPLIER_BPS, TENURE_TIER_SECONDS

from .. import txbuild
from ..borsh import BorshError, b58decode
from ..config import Settings
from ..store import Store
from .events import render_event_html
from .format import FILTERS, ago, duration, short

TEMPLATES_DIR = "templates"

FEED_FILTERS = [
    {"label": "Everything", "kinds": []},
    {"label": "Registrations", "kinds": ["MintRegistered", "WalletRegistered"]},
    {"label": "Weight syncs", "kinds": ["WeightSynced"]},
    {"label": "Donations", "kinds": ["FeeCollected"]},
    {"label": "Claims", "kinds": ["RewardClaimed"]},
    {"label": "Deficit alerts", "kinds": ["VaultDeficitDetected"]},
]

TENURE_TIERS = list(zip((0, *TENURE_TIER_SECONDS), TENURE_TIER_MULTIPLIER_BPS))


def register(app, settings: Settings, store: Store, mode: str = "devnet") -> None:
    """Mount the UI onto an existing FastAPI app."""
    here = pathlib.Path(__file__).parent
    templates = Jinja2Templates(directory=str(here / TEMPLATES_DIR))
    templates.env.filters.update(FILTERS)
    app.mount("/static", StaticFiles(directory=str(here / "static")), name="static")

    def base_context(request: Request, page: str) -> Dict[str, Any]:
        return {"request": request, "page": page, "mode": mode}

    def decorate_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for event in events:
            row = dict(event)
            row["html"] = render_event_html(event)
            out.append(row)
        return out

    # -- pages --------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def page_home(request: Request):
        tokens = sorted(store.list_tokens(), key=lambda t: t["registeredAt"], reverse=True)
        return templates.TemplateResponse(
            request, "index.html", {**base_context(request, "home"), "tokens": tokens}
        )

    @app.get("/token/{mint}", response_class=HTMLResponse, include_in_schema=False)
    async def page_token(request: Request, mint: str):
        view = store.token_view(mint)
        if view is None:
            raise HTTPException(status_code=404, detail="unknown mint")
        registrations = sorted(
            store.registrations_for_mint(mint), key=lambda r: r["weightedShares"], reverse=True
        )[:20]
        pending = store.pending_registrations_for_mint(mint)
        events = decorate_events(store.feed_view(mint=mint, limit=15))
        return templates.TemplateResponse(
            request,
            "token.html",
            {
                **base_context(request, "token"),
                "token": view,
                "registrations": registrations,
                "pending": pending,
                "events": events,
                "tenure_tiers": TENURE_TIERS,
            },
        )

    @app.get("/feed", response_class=HTMLResponse, include_in_schema=False)
    async def page_feed(request: Request):
        return templates.TemplateResponse(
            request,
            "feed.html",
            {
                **base_context(request, "feed"),
                "filters": FEED_FILTERS,
                "events": decorate_events(store.feed_view(limit=150)),
            },
        )

    # -- JSON helpers for the browser ---------------------------------------

    @app.get("/api/registration-card/{mint}/{owner}", include_in_schema=False)
    async def api_registration_card(request: Request, mint: str, owner: str):
        view = store.registration_view(mint, owner)
        if view is None:
            return {
                "html": '<p class="hint">No registration yet. Send the marker amount to the '
                "deposit vault above, then wait for the operator to write it.</p>",
                "summary": "",
            }
        html = templates.get_template("_registration_card.html").render(r=view, request=request)
        return {
            "html": html,
            "summary": f"registered {duration(view['ageSeconds'])} ago",
        }

    @app.post("/api/render-event", include_in_schema=False)
    async def api_render_event(event: Dict[str, Any]):
        data = event.get("data", {}) or {}
        return {
            "html": str(render_event_html(event)),
            "when": ago(data.get("timestamp")) if data.get("timestamp") else f"slot {event.get('slot', 0)}",
            "where": short(data.get("mint"), 4, 3) if data.get("mint") else "",
        }

    # -- the wallet flow ----------------------------------------------------

    @app.post("/api/tx/{action}", include_in_schema=False)
    async def api_build_tx(action: str, payload: Dict[str, Any]):
        """Build an **unsigned** transaction message for the wallet to sign."""
        builder = txbuild.BUILDERS.get(action)
        if builder is None:
            raise HTTPException(status_code=404, detail=f"unknown action {action!r}")

        wallet = payload.get("wallet")
        if not wallet:
            raise HTTPException(status_code=400, detail="no wallet connected")
        for key in ("wallet", "mint", "owner", "authority", "creator", "newAuthority"):
            value = payload.get(key)
            if not value:
                continue
            try:
                decoded = b58decode(str(value))
            except BorshError as exc:
                raise HTTPException(
                    status_code=400, detail=f"{key} is not a valid address: {exc}"
                ) from exc
            if len(decoded) != 32:
                raise HTTPException(status_code=400, detail=f"{key} is not a valid address")

        program_id = settings.program_id
        try:
            if action == "initialize_config":
                instruction = builder(program_id, wallet, payload["authority"])
            elif action == "update_authority":
                instruction = builder(program_id, wallet, payload["newAuthority"])
            elif action == "register_mint":
                instruction = builder(program_id, wallet, payload["mint"], payload["creator"])
            elif action == "write_registration":
                instruction = builder(program_id, wallet, payload["owner"], payload["mint"])
            elif action in ("sync", "claim"):
                owner = payload.get("owner", wallet) if action == "sync" else wallet
                token_program = await txbuild.mint_token_program(settings.rpc_http, payload["mint"])
                if action == "sync":
                    instruction = builder(program_id, wallet, owner, payload["mint"], token_program)
                else:
                    instruction = builder(program_id, wallet, payload["mint"], token_program)
            elif action == "donate":
                instruction = builder(program_id, wallet, payload["mint"], int(payload["amount"]))
            else:
                raise HTTPException(status_code=404, detail=f"unknown action {action!r}")
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError, txbuild.TxBuildError) as exc:
            raise HTTPException(status_code=400, detail=f"bad parameters: {exc}") from exc

        try:
            blockhash = await txbuild.latest_blockhash(settings.rpc_http)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"could not reach {settings.rpc_http} for a blockhash: {exc}",
            ) from exc

        return JSONResponse(
            {
                "action": action,
                "message": txbuild.build_message_b58(wallet, [instruction], blockhash),
                "programId": program_id,
                "blockhash": blockhash,
            }
        )
