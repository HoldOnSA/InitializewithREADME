"""The devnet subscription loop.

Three sources feed the store:

* `logsSubscribe` mentioning the program - the event stream (registrations,
  syncs, fee collections, claims). Low latency, but logs can be dropped on
  reconnect.
* `programSubscribe` plus a periodic `getProgramAccounts` sweep - the account
  state (GlobalConfig, TokenConfig, DepositVault, Registration, LoyaltyPool).
  This is the authoritative view and it backfills anything the log stream
  missed.
* A periodic sweep of every known `DepositVault` address for marker-transfer
  *candidates* - plain SOL transfers that never mention the program at all
  (there is no program interaction in sending a marker; see
  `constants.py`'s `REGISTRATION_MARKER_LAMPORTS` doc), so `logsSubscribe`
  can never see them. This never writes a `Registration` itself - it only
  queues a `PendingRegistration` for the operator (holding
  `GlobalConfig.authority`) to review and sign `write_registration` for,
  same as any other wallet-signed action here. See `SECURITY_NOTES.md`.

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
from typing import Any, Dict, List, Optional, Set

from stackapp_sim.constants import REGISTRATION_MARKER_LAMPORTS

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
        # Signatures already inspected for a marker transfer, per vault, so a
        # sweep never re-fetches a transaction it has already judged.
        self._seen_marker_signatures: Dict[str, Set[str]] = {}

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
            await self.refresh_marker_candidates()

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
            self.store.apply_account(
                decoded["name"], decoded["data"], address, slot, lamports=account.get("lamports", 0)
            )

    # -- periodic account sweep --------------------------------------------

    async def _run_refresh(self) -> None:
        while True:
            await asyncio.sleep(self.settings.refresh_interval)
            try:
                await self.refresh_accounts()
                await self.refresh_marker_candidates()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("account refresh failed: %s", exc)

    async def _rpc(self, method: str, params: list) -> Any:
        import httpx

        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.settings.rpc_http, json=payload)
            response.raise_for_status()
            body = response.json()
        if "error" in body:
            raise RuntimeError(f"{method} failed: {body['error']}")
        return body.get("result")

    async def refresh_accounts(self) -> int:
        """Full `getProgramAccounts` sweep. Returns how many were decoded."""
        result = await self._rpc(
            "getProgramAccounts",
            [self.settings.program_id, {"encoding": "base64", "commitment": "confirmed", "withContext": True}],
        )
        context = result.get("context", {}) if isinstance(result, dict) else {}
        slot = context.get("slot", 0)
        accounts = result.get("value", result) if isinstance(result, dict) else result

        count = 0
        for entry in accounts or []:
            self._ingest_account(entry.get("pubkey", ""), entry.get("account", {}), slot)
            count += 1
        log.info("refreshed %d program accounts at slot %s", count, slot)
        return count

    # -- marker-transfer candidates ------------------------------------------

    async def refresh_marker_candidates(self) -> int:
        """Look for plain SOL transfers into each known `DepositVault` that
        match `REGISTRATION_MARKER_LAMPORTS` exactly, and queue them as
        `PendingRegistration`s. Returns how many new candidates were found.

        This is a heuristic, not a proof: it assumes the marker's sender is
        the transaction's fee payer (account index 0), which is true for the
        simple "send SOL from your own wallet" flow the design calls for, but
        would misattribute a marker relayed through some other fee payer.
        Either way this only ever *proposes* a registration for a human to
        approve - see the module docstring.
        """
        found = 0
        for mint, config in list(self.store.configs.items()):
            vault = config["deposit_vault"]
            seen = self._seen_marker_signatures.setdefault(vault, set())
            signatures = await self._rpc(
                "getSignaturesForAddress", [vault, {"limit": 25, "commitment": "confirmed"}]
            )
            for entry in reversed(signatures or []):  # oldest first
                signature = entry.get("signature")
                if not signature or signature in seen or entry.get("err"):
                    continue
                seen.add(signature)
                try:
                    owner, amount, slot = await self._inspect_transfer(vault, signature)
                except Exception as exc:  # noqa: BLE001
                    log.debug("could not inspect %s: %s", signature, exc)
                    continue
                if owner is not None and amount == REGISTRATION_MARKER_LAMPORTS:
                    self.store.note_marker_candidate(mint, owner, amount, slot, signature)
                    found += 1
        return found

    async def _inspect_transfer(self, vault: str, signature: str):
        tx = await self._rpc(
            "getTransaction",
            [signature, {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
        )
        if tx is None:
            return None, 0, 0
        message = tx["transaction"]["message"]
        keys = message.get("accountKeys", [])
        meta = tx.get("meta") or {}
        pre = meta.get("preBalances", [])
        post = meta.get("postBalances", [])
        if vault not in keys or not pre or not post:
            return None, 0, 0
        idx = keys.index(vault)
        delta = post[idx] - pre[idx]
        slot = tx.get("slot", 0)
        # The fee payer (account 0) is the sender for the plain, single-wallet
        # transfer the marker flow calls for.
        sender = keys[0] if keys else None
        return sender, delta, slot

    def health(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "lastError": self.last_error,
            "rpcHttp": self.settings.rpc_http,
            "rpcWs": self.settings.rpc_ws,
            "programId": self.settings.program_id,
        }
