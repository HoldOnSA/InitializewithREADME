"""Indexer configuration. Devnet only - there is no mainnet path here."""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

DEFAULT_PROGRAM_ID = "Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS"

# Guard rail: this prototype refuses to point at mainnet. See README.
BLOCKED_HOSTS = (
    "api.mainnet-beta.solana.com",
    "mainnet.helius-rpc.com",
    "solana-mainnet",
)


class MainnetRefused(RuntimeError):
    pass


@dataclass
class Settings:
    program_id: str = DEFAULT_PROGRAM_ID
    rpc_http: str = "https://api.devnet.solana.com"
    rpc_ws: str = "wss://api.devnet.solana.com"
    host: str = "127.0.0.1"
    port: int = 8787
    # Comma-separated origins allowed to call the API.
    cors_origins: str = "http://localhost:3000"
    # Poll interval (seconds) for the periodic account refresh that backfills
    # anything the log stream missed.
    refresh_interval: float = 20.0
    idl_path: pathlib.Path = pathlib.Path("target/idl/stackapp.json")

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            program_id=os.getenv("STACKAPP_PROGRAM_ID", DEFAULT_PROGRAM_ID),
            rpc_http=os.getenv("STACKAPP_RPC_HTTP", "https://api.devnet.solana.com"),
            rpc_ws=os.getenv("STACKAPP_RPC_WS", "wss://api.devnet.solana.com"),
            host=os.getenv("STACKAPP_HOST", "127.0.0.1"),
            port=int(os.getenv("STACKAPP_PORT", "8787")),
            cors_origins=os.getenv("STACKAPP_CORS_ORIGINS", "http://localhost:3000"),
            refresh_interval=float(os.getenv("STACKAPP_REFRESH_INTERVAL", "20")),
            idl_path=pathlib.Path(
                os.getenv("STACKAPP_IDL", "target/idl/stackapp.json")
            ),
        )
        settings.assert_not_mainnet()
        return settings

    def assert_not_mainnet(self) -> None:
        for endpoint in (self.rpc_http, self.rpc_ws):
            lowered = endpoint.lower()
            if any(blocked in lowered for blocked in BLOCKED_HOSTS):
                raise MainnetRefused(
                    f"{endpoint} looks like mainnet. StackApp is an unaudited "
                    "prototype and has no mainnet path; see README."
                )

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
