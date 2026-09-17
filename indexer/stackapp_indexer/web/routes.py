"""The Python-served UI.

Server-rendered Jinja pages plus a thin JSON layer for the wallet flow. No
bundler, no Node, no npm - the whole prototype runs on Python.

The wallet flow is:

    browser  POST /api/tx/<action> {wallet, ...}
    server   builds an UNSIGNED transaction message with txbuild.py
    browser  window.solana.request({method:"signAndSendTransaction", ...})

The server never sees a private key or a signature.
"""

from __future__ import annotations

import hashlib
import pathlib
import secrets
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import txbuild
from ..borsh import BorshError, b58decode, b58encode
from ..config import Settings
from ..store import Store
from .events import render_event_html
from .format import FILTERS, ago, bps, duration, short, tax_curve_chart

TEMPLATES_DIR = "templates"

TIER_LADDER = [
    {"tier": 0, "name": "Drifter", "at": 0},
    {"tier": 1, "name": "Holder", "at": 1_000},
    {"tier": 2, "name": "Anchor", "at": 10_000},
    {"tier": 3, "name": "Keystone", "at": 50_000},
    {"tier": 4, "name": "Bedrock", "at": 250_000},
]

VEST_OPTIONS = [
    {"label": "None", "seconds": 0},
    {"label": "1 day", "seconds": 86_400},
    {"label": "3 days", "seconds": 259_200},
    {"label": "1 week", "seconds": 604_800},
    {"label": "30 days", "seconds": 2_592_000},
    {"label": "90 days", "seconds": 7_776_000},
]

FEED_FILTERS = [
    {"label": "Everything", "kinds": []},
    {"label": "Buys", "kinds": ["BuyExecuted"]},
    {"label": "Sells & transfers", "kinds": ["ExitExecuted"]},
    {"label": "Tax → pool", "kinds": ["TaxCollected"]},
    {"label": "Pool claims", "kinds": ["PoolClaimed"]},
    {"label": "Tier-ups", "kinds": ["TierUp", "ReputationUpdated"]},
    {"label": "Launches", "kinds": ["LaunchInitialized"]},
]

PRESETS = {
    "diamond": {
        "label": "Diamond hands",
        "blurb": "30% to start, free after a week. The default shape.",
        "points": [
            {"secondsHeld": 0, "taxBps": 3_000},
            {"secondsHeld": 3_600, "taxBps": 2_000},
            {"secondsHeld": 86_400, "taxBps": 1_000},
            {"secondsHeld": 604_800, "taxBps": 0},
        ],
    },
    "gentle": {
        "label": "Gentle",
        "blurb": "10% to start. Discourages flipping without punishing it.",
        "points": [
            {"secondsHeld": 0, "taxBps": 1_000},
            {"secondsHeld": 3_600, "taxBps": 500},
            {"secondsHeld": 86_400, "taxBps": 200},
            {"secondsHeld": 604_800, "taxBps": 0},
        ],
    },
    "brutal": {
        "label": "Brutal",
        "blurb": "90% to start and never reaches zero. For long-horizon launches.",
        "points": [
            {"secondsHeld": 0, "taxBps": 9_000},
            {"secondsHeld": 3_600, "taxBps": 6_000},
            {"secondsHeld": 86_400, "taxBps": 3_000},
            {"secondsHeld": 2_592_000, "taxBps": 500},
        ],
    },
    "flat": {
        "label": "Flat",
        "blurb": "A constant 5%. No tenure incentive at all - useful as a control.",
        "points": [{"secondsHeld": 0, "taxBps": 500}],
    },
}


def validate_tax_curve(points: List[Dict[str, int]]) -> Optional[str]:
    """Mirrors `math::validate_tax_curve`, so the form can reject a bad curve
    before it costs a transaction."""
    if not points:
        return "A curve needs at least one point."
    if len(points) > 8:
        return "At most 8 points."
    if int(points[0]["secondsHeld"]) != 0:
        return "The first point must be at 0 seconds held."
    for i, point in enumerate(points):
        if int(point["taxBps"]) > 9_000:
            return "Tax cannot exceed 90%."
        if int(point["taxBps"]) < 0:
            return "Tax cannot be negative."
        if i > 0:
            if int(point["secondsHeld"]) <= int(points[i - 1]["secondsHeld"]):
                return "Points must increase in seconds held."
            if int(point["taxBps"]) > int(points[i - 1]["taxBps"]):
                return "Tax must never rise with time held - that would reward selling sooner."
    return None


def suggest_mint() -> str:
    """A fresh address to use as a launch seed.

    The program takes `mint` as an `UncheckedAccount` used only for PDA
    derivation, so it never signs and never has to exist on chain.
    """
    return b58encode(hashlib.sha256(secrets.token_bytes(32)).digest())


def register(app, settings: Settings, store: Store, mode: str = "devnet") -> None:
    """Mount the UI onto an existing FastAPI app."""
    here = pathlib.Path(__file__).parent
    templates = Jinja2Templates(directory=str(here / TEMPLATES_DIR))
    templates.env.filters.update(FILTERS)
    app.mount("/static", StaticFiles(directory=str(here / "static")), name="static")

    def base_context(request: Request, page: str) -> Dict[str, Any]:
        return {"request": request, "page": page, "mode": mode}

    def decorate_token(view: Dict[str, Any]) -> Dict[str, Any]:
        view = dict(view)
        view["chart"] = tax_curve_chart(view.get("taxCurve", []), chart_id=view["mint"][:6])
        return view

    def decorate_events(events: List[Dict[str, Any]], decimals: int = 6) -> List[Dict[str, Any]]:
        out = []
        for event in events:
            row = dict(event)
            row["html"] = render_event_html(event, decimals)
            out.append(row)
        return out

    # -- pages --------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def page_home(request: Request):
        tokens = sorted(store.list_tokens(), key=lambda t: t["launchTimestamp"], reverse=True)
        return templates.TemplateResponse(
            request,
            "index.html",
            {**base_context(request, "home"), "tokens": [decorate_token(t) for t in tokens]},
        )

    @app.get("/launch", response_class=HTMLResponse, include_in_schema=False)
    async def page_launch(request: Request):
        import json

        return templates.TemplateResponse(
            request,
            "launch.html",
            {
                **base_context(request, "launch"),
                "presets": PRESETS,
                "presets_json": json.dumps(PRESETS),
                "vest_options": VEST_OPTIONS,
                "suggested_mint": suggest_mint(),
            },
        )

    @app.get("/token/{mint}", response_class=HTMLResponse, include_in_schema=False)
    async def page_token(request: Request, mint: str):
        view = store.token_view(mint)
        if view is None:
            raise HTTPException(status_code=404, detail="unknown mint")
        decimals = view.get("decimals", 6)
        holders = sorted(
            store.positions_for_mint(mint), key=lambda p: p["weightedShares"], reverse=True
        )[:12]
        events = decorate_events(store.feed_view(mint=mint, limit=15), decimals)
        return templates.TemplateResponse(
            request,
            "token.html",
            {
                **base_context(request, "token"),
                "token": decorate_token(view),
                "holders": holders,
                "events": events,
            },
        )

    @app.get("/passport/{wallet}", response_class=HTMLResponse, include_in_schema=False)
    async def page_passport(request: Request, wallet: str):
        passport = store.reputation_view(wallet)
        score = int(passport["score"])
        floor = TIER_LADDER[min(passport["tier"], len(TIER_LADDER) - 1)]["at"]
        nxt = int(passport["nextTierAt"]) if passport["nextTierAt"] else None
        progress = 10_000 if not nxt else int((score - floor) * 10_000 / max(nxt - floor, 1))
        return templates.TemplateResponse(
            request,
            "passport.html",
            {
                **base_context(request, "passport"),
                "passport": passport,
                "score": score,
                "ladder": TIER_LADDER,
                "tier_progress_bps": max(0, min(10_000, progress)),
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

    @app.get("/api/suggest-mint", include_in_schema=False)
    async def api_suggest_mint():
        return {"mint": suggest_mint()}

    @app.post("/api/preview", include_in_schema=False)
    async def api_preview(payload: Dict[str, Any]):
        points = payload.get("points") or []
        error = validate_tax_curve(points)
        chart = tax_curve_chart(points, chart_id="preview")
        macros = templates.env.get_template("_macros.html").module
        return {
            "error": error,
            "chartHtml": str(macros.tax_chart(chart)) if chart else "",
            "tableHtml": str(macros.tax_table(points)),
            "vestLabel": duration(payload.get("vestSeconds", 0)) if payload.get("vestSeconds") else "none",
            "openingLabel": f"{bps(points[0]['taxBps'])} tax" if points else "-",
            "floorLabel": f"{bps(points[-1]['taxBps'])} tax" if points else "-",
        }

    @app.get("/api/position-card/{mint}/{owner}", include_in_schema=False)
    async def api_position_card(request: Request, mint: str, owner: str):
        view = store.position_view(mint, owner)
        if view is None:
            return {
                "html": '<p class="hint">No position yet. Buy some tokens below to open one.</p>',
                "summary": "",
            }
        token = store.token_view(mint) or {}
        decimals = token.get("decimals", 6)
        html = templates.get_template("_position_card.html").render(
            p=view, decimals=decimals, request=request
        )
        lots = len(view["lots"])
        return {
            "html": html,
            "summary": f"{lots} lot{'' if lots == 1 else 's'} · "
            f"first bought {duration(store.now() - view['firstBuyTimestamp'])} ago",
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
        for key in ("wallet", "mint", "recipient"):
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
            if action == "initialize_launch":
                points = payload.get("taxCurve") or []
                error = validate_tax_curve(points)
                if error:
                    raise HTTPException(status_code=400, detail=error)
                instruction = builder(
                    program_id,
                    wallet,
                    payload["mint"],
                    [(int(p["secondsHeld"]), int(p["taxBps"])) for p in points],
                    int(payload.get("vestDurationSeconds", 0)),
                )
            elif action in ("buy", "sell"):
                instruction = builder(
                    program_id, wallet, payload["mint"], int(payload["amount"])
                )
            elif action == "transfer_position":
                instruction = builder(
                    program_id,
                    wallet,
                    payload["recipient"],
                    payload["mint"],
                    int(payload["amount"]),
                )
            elif action == "donate_to_pool":
                instruction = builder(
                    program_id, wallet, payload["mint"], int(payload["amount"])
                )
            elif action == "sync_weight":
                instruction = builder(
                    program_id, wallet, payload.get("owner", wallet), payload["mint"]
                )
            else:
                instruction = builder(program_id, wallet, payload["mint"])
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError) as exc:
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
