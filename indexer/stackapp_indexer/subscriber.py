"""The devnet subscription loop.

Two sources feed the store:

* `logsSubscribe` mentioning the program - the event stream (buys, sells, tax,
  claims, tier-ups). Low latency, but logs can be dropped on reconnect.
* `programSubscribe` plus a periodic `getProgramAccounts` sweep - the account
  state (TokenConfig, Position, LoyaltyPool, Reputation). This is the
  authoritative view and it backfills anything the log stream missed.

RPC is spoken as plain JSON-RPC over `websockets` / `httpx`. That keeps the
dependency surface small and means the wire format is visible in this file
rather than hidden behind a client library.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import Any, Dict, List, Optional

from .config import Settings
from .layouts import decode_account, parse_program_data_lines
from .store import Store

log = logging.getLogger("stackapp.subscriber")


class Subscriber:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self._tasks: List[asyncio.Task] = []
        self._request_id = 0
        self.connected = False
        self.last_error: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._run_websocket(), name="stackapp-ws"),
            asyncio.create_task(self._run_refresh(), name="stackapp-refresh"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    # -- websocket ----------------------------------------------------------

    async def _run_websocket(self) -> None:
        """Reconnect forever with capped exponential backoff."""
        backoff = 1.0
        while True:
            try:
                await self._websocket_session()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("websocket dropped (%s); retrying in %.1fs", self.last_error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _websocket_session(self) -> None:
        import websockets

        async with websockets.connect(self.settings.rpc_ws, max_size=16 * 1024 * 1024) as ws:
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": self._next_id(),
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [self.settings.program_id]},
                            {"commitment": "confirmed"},
                        ],
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": self._next_id(),
                        "method": "programSubscribe",
                        "params": [
                            self.settings.program_id,
                            {"commitment": "confirmed", "encoding": "base64"},
                        ],
                    }
                )
            )
            self.connected = True
            self.last_error = None
            log.info("subscribed to %s on %s", self.settings.program_id, self.settings.rpc_ws)

            # A backfill on (re)connect closes whatever gap the outage left.
            await self.refresh_accounts()

            async for raw in ws:
                try:
                    self._handle_message(json.loads(raw))
                except Exception as exc:  # noqa: BLE001
                    log.warning("failed to handle message: %s", exc)

    def _handle_message(self, message: Dict[str, Any]) -> None:
        params = message.get("params")
        if not params:
            return  # subscription confirmations
        result = params.get("result", {})
        context = result.get("context", {})
        slot = context.get("slot", 0)
        value = result.get("value", {})

        if "logs" in value:
            self._handle_logs(value, slot)
        elif "account" in value:
            self._handle_account(value, slot)

    def _handle_logs(self, value: Dict[str, Any], slot: int) -> None:
        if value.get("err"):
            return  # failed transactions changed nothing
        signature = value.get("signature", "")
        for event in parse_program_data_lines(value.get("logs", [])):
            self.store.apply_event(event["name"], event["data"], slot=slot, signature=signature)

    def _handle_account(self, value: Dict[str, Any], slot: int) -> None:
        account = value.get("account", {})
        address = value.get("pubkey", "")
        self._ingest_account(address, account, slot)

    def _ingest_account(self, address: str, account: Dict[str, Any], slot: int) -> None:
        data = account.get("data")
        if isinstance(data, list) and data and data[1] == "base64":
            raw = base64.b64decode(data[0])
        elif isinstance(data, str):
            raw = base64.b64decode(data)
        else:
            return
        decoded = decode_account(raw)
        if decoded is not None:
            self.store.apply_account(decoded["name"], decoded["data"], address, slot)

    # -- periodic account sweep --------------------------------------------

    async def _run_refresh(self) -> None:
        while True:
            await asyncio.sleep(self.settings.refresh_interval)
            try:
                await self.refresh_accounts()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("account refresh failed: %s", exc)

    async def refresh_accounts(self) -> int:
        """Full `getProgramAccounts` sweep. Returns how many were decoded."""
        import httpx

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "getProgramAccounts",
            "params": [
                self.settings.program_id,
                {"encoding": "base64", "commitment": "confirmed", "withContext": True},
            ],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.settings.rpc_http, json=payload)
            response.raise_for_status()
            body = response.json()

        if "error" in body:
            raise RuntimeError(f"getProgramAccounts failed: {body['error']}")

        result = body.get("result", {})
        context = result.get("context", {}) if isinstance(result, dict) else {}
        slot = context.get("slot", 0)
        accounts = result.get("value", result) if isinstance(result, dict) else result

        count = 0
        for entry in accounts or []:
            self._ingest_account(entry.get("pubkey", ""), entry.get("account", {}), slot)
            count += 1
        log.info("refreshed %d program accounts at slot %s", count, slot)
        return count

    def health(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "lastError": self.last_error,
            "rpcHttp": self.settings.rpc_http,
            "rpcWs": self.settings.rpc_ws,
            "programId": self.settings.program_id,
        }
