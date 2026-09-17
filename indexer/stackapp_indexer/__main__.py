"""Run the indexer.

    python -m stackapp_indexer                 # live devnet
    python -m stackapp_indexer --mock          # synthetic feed, no network
    python -m stackapp_indexer --check-layouts # decode self-test, then exit
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import MainnetRefused, Settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="stackapp-indexer")
    parser.add_argument("--host", help="bind address")
    parser.add_argument("--port", type=int, help="bind port")
    parser.add_argument("--rpc-http", help="devnet HTTP RPC endpoint")
    parser.add_argument("--rpc-ws", help="devnet WebSocket RPC endpoint")
    parser.add_argument("--program-id", help="deployed program id")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="serve a synthetic feed instead of subscribing (no network needed)",
    )
    parser.add_argument(
        "--check-layouts",
        action="store_true",
        help="round-trip every event and account layout, then exit",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )

    if args.check_layouts:
        from .selftest import check_layouts

        return check_layouts()

    try:
        settings = Settings.from_env()
    except MainnetRefused as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2

    for attr in ("host", "port", "rpc_http", "rpc_ws", "program_id"):
        value = getattr(args, attr, None)
        if value:
            setattr(settings, attr, value)
    try:
        settings.assert_not_mainnet()
    except MainnetRefused as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2

    import uvicorn

    if args.mock:
        from .mock import create_mock_app

        app = create_mock_app(settings)
        print("StackApp indexer - MOCK mode, no network, synthetic data")
    else:
        from .api import create_app

        app = create_app(settings)
        print(f"StackApp indexer - devnet, program {settings.program_id}")

    print(f"  REST  http://{settings.host}:{settings.port}")
    print(f"  WS    ws://{settings.host}:{settings.port}/ws")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
