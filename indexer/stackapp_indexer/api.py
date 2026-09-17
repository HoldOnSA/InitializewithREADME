"""REST + WebSocket API.

    GET  /api/health
    GET  /api/tokens
    GET  /api/tokens/{mint}
    GET  /api/tokens/{mint}/holders
    GET  /api/positions/{owner}
    GET  /api/positions/{mint}/{owner}
    GET  /api/passport/{wallet}
    GET  /api/feed?limit=&mint=&kind=&owner=
    GET  /api/pdas/{mint}?owner=
    WS   /ws                 - live event feed, newest first

The JSON API lives under /api because the bare paths (/feed, /passport/{wallet},
/token/{mint}) are the Python-served HTML pages - see web/routes.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Dict, List, Optional

# These must be module-level: `from __future__ import annotations` turns every
# annotation into a string, and FastAPI resolves them against module globals.
# Importing them inside create_app() leaves `WebSocket` unresolvable, which
# silently turns the /ws handler into a normal endpoint and 403s the handshake.
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .layouts import EVENT_LAYOUTS, load_idl_overrides
from .pda import (
    curve_vault_pda,
    loyalty_pool_pda,
    position_pda,
    reputation_pda,
    token_config_pda,
)
from .store import Store
from .subscriber import Subscriber

log = logging.getLogger("stackapp.api")


def create_app(
    settings: Optional[Settings] = None,
    store: Optional[Store] = None,
    mode: str = "devnet",
):
    settings = settings or Settings.from_env()
    settings.assert_not_mainnet()
    store = store if store is not None else Store()
    subscriber = Subscriber(settings, store)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        for problem in load_idl_overrides(settings.idl_path):
            log.warning("IDL check: %s", problem)
        await subscriber.start()
        try:
            yield
        finally:
            await subscriber.stop()

    app = FastAPI(
        title="StackApp indexer",
        version="0.1.0",
        description=(
            "Devnet prototype indexer for the StackApp launchpad. "
            "Unaudited; no mainnet path."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    app.state.settings = settings
    app.state.store = store
    app.state.subscriber = subscriber
    app.state.mode = mode

    # The Python-served UI owns the bare paths; the JSON API lives under /api,
    # so the two cannot shadow each other whatever order they register in.
    from .web import register as register_web

    register_web(app, settings, store, mode=mode)

    @app.get("/api/health")
    async def health() -> Dict[str, Any]:
        return {
            "ok": True,
            "cluster": "devnet",
            "prototype": True,
            "subscriber": subscriber.health(),
            "store": store.stats(),
            "knownEvents": sorted(EVENT_LAYOUTS),
        }

    @app.get("/api/tokens")
    async def tokens() -> List[Dict[str, Any]]:
        return sorted(
            store.list_tokens(), key=lambda t: t["launchTimestamp"], reverse=True
        )

    @app.get("/api/tokens/{mint}")
    async def token(mint: str) -> Dict[str, Any]:
        view = store.token_view(mint)
        if view is None:
            raise HTTPException(status_code=404, detail="unknown mint")
        return view

    @app.get("/api/tokens/{mint}/holders")
    async def holders(mint: str, limit: int = Query(100, ge=1, le=1000)) -> List[Dict[str, Any]]:
        rows = store.positions_for_mint(mint)
        rows.sort(key=lambda p: p["weightedShares"], reverse=True)
        return rows[:limit]

    @app.get("/api/positions/{owner}")
    async def positions(owner: str) -> List[Dict[str, Any]]:
        return store.positions_for_owner(owner)

    @app.get("/api/positions/{mint}/{owner}")
    async def position(mint: str, owner: str) -> Dict[str, Any]:
        view = store.position_view(mint, owner)
        if view is None:
            raise HTTPException(status_code=404, detail="no position")
        return view

    @app.get("/api/passport/{wallet}")
    async def passport(wallet: str) -> Dict[str, Any]:
        return store.reputation_view(wallet)

    @app.get("/api/feed")
    async def feed(
        limit: int = Query(100, ge=1, le=1000),
        mint: Optional[str] = None,
        owner: Optional[str] = None,
        kind: Optional[List[str]] = Query(None),
    ) -> List[Dict[str, Any]]:
        return store.feed_view(limit=limit, mint=mint, kinds=kind, owner=owner)

    @app.get("/api/pdas/{mint}")
    async def pdas(mint: str, owner: Optional[str] = None) -> Dict[str, Any]:
        """Addresses the frontend needs to build instructions client-side."""
        program_id = settings.program_id
        try:
            config, config_bump = token_config_pda(mint, program_id)
            pool, pool_bump = loyalty_pool_pda(mint, program_id)
            vault, vault_bump = curve_vault_pda(mint, program_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"bad mint: {exc}") from exc

        out: Dict[str, Any] = {
            "programId": program_id,
            "tokenConfig": {"address": config, "bump": config_bump},
            "loyaltyPool": {"address": pool, "bump": pool_bump},
            "curveVault": {"address": vault, "bump": vault_bump},
        }
        if owner:
            try:
                pos, pos_bump = position_pda(mint, owner, program_id)
                rep, rep_bump = reputation_pda(owner, program_id)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"bad owner: {exc}") from exc
            out["position"] = {"address": pos, "bump": pos_bump}
            out["reputation"] = {"address": rep, "bump": rep_bump}
        return out

    @app.websocket("/ws")
    async def websocket_feed(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = store.subscribe()
        try:
            # Prime the connection with recent history, newest first.
            await websocket.send_json({"type": "backlog", "events": store.feed_view(limit=50)})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
                    continue
                await websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        finally:
            store.unsubscribe(queue)

    return app
