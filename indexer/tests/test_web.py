"""The Python-served UI: formatting, rendering, and every route.

Two bugs that these lock down, both caused by `from __future__ import
annotations` turning annotations into strings that FastAPI must resolve against
*module* globals:

* importing `Request` inside `register()` made every page a 422
* importing `WebSocket` inside `create_app()` silently 403'd the /ws handshake

Neither showed up as an import error, so they need route-level coverage.
"""

import asyncio
import unittest

from stackapp_indexer.config import Settings
from stackapp_indexer.mock import MockDriver
from stackapp_indexer.store import Store
from stackapp_indexer.web.events import render_event_html
from stackapp_indexer.web.format import (
    ago,
    bps,
    duration,
    pct,
    price,
    short,
    sol,
    tax_curve_chart,
    tokens,
)
from stackapp_indexer.web.routes import PRESETS, suggest_mint, validate_tax_curve

CURVE = [
    {"secondsHeld": 0, "taxBps": 3_000},
    {"secondsHeld": 3_600, "taxBps": 2_000},
    {"secondsHeld": 86_400, "taxBps": 1_000},
    {"secondsHeld": 604_800, "taxBps": 0},
]


class TestFormat(unittest.TestCase):
    def test_duration_reads_naturally(self):
        self.assertEqual(duration(0), "0s")
        self.assertEqual(duration(45), "45s")
        self.assertEqual(duration(90), "1m 30s")
        self.assertEqual(duration(3_600), "1h")
        self.assertEqual(duration(86_400), "1d")
        self.assertEqual(duration(604_800), "1w")
        self.assertEqual(duration(2_592_000), "1mo")
        self.assertEqual(duration(None), "-")

    def test_duration_stops_at_two_units(self):
        self.assertEqual(len(duration(86_400 + 3_600 + 90).split()), 2)

    def test_sol_and_tokens(self):
        self.assertEqual(sol(1_000_000_000), "1 SOL")
        self.assertEqual(sol(1_500_000_000), "1.5 SOL")
        self.assertEqual(sol(1), "<0.0001 SOL")
        self.assertEqual(sol(0), "0 SOL")
        self.assertEqual(tokens(1_000_000), "1.00")
        self.assertEqual(tokens(1_000_000_000), "1.00K")
        self.assertEqual(tokens(1_000_000_000_000), "1.00M")

    def test_bps_and_pct(self):
        self.assertEqual(bps(3_000), "30%")
        self.assertEqual(bps(250), "2.5%")
        self.assertEqual(bps(0), "0%")
        self.assertEqual(pct(5_000), "50.00%")
        self.assertEqual(pct(-10), "0.00%", "a progress bar never goes negative")
        self.assertEqual(pct(99_999), "100.00%", "or past full")

    def test_short_and_price(self):
        self.assertEqual(short("abcdefghijklmnop"), "abcd…mnop")
        self.assertEqual(short("abc"), "abc")
        self.assertEqual(short(None), "-")
        self.assertEqual(price(27), "27 lamports")
        self.assertIn("SOL", price(50_000_000))

    def test_ago_handles_junk(self):
        self.assertEqual(ago(None), "-")
        self.assertEqual(ago("nonsense"), "-")


class TestChart(unittest.TestCase):
    def test_empty_curve_has_no_chart(self):
        self.assertIsNone(tax_curve_chart([]))

    def test_paths_stay_inside_the_viewbox(self):
        chart = tax_curve_chart(CURVE)
        numbers = []
        for token in chart["line"].replace("M", " ").replace("L", " ").split():
            x, _, y = token.partition(",")
            numbers.append((float(x), float(y)))
        for x, y in numbers:
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x, 320)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(y, 96)

    def test_the_curve_descends(self):
        """Tax falls as time held rises, so the path must move downward."""
        chart = tax_curve_chart(CURVE)
        ys = [
            float(t.partition(",")[2])
            for t in chart["line"].replace("M", " ").replace("L", " ").split()
        ]
        self.assertGreater(ys[-1], ys[0], "later points sit lower on the chart (less tax)")

    def test_fill_path_is_closed(self):
        self.assertTrue(tax_curve_chart(CURVE)["fill"].endswith("Z"))

    def test_single_point_curve_is_flat_and_valid(self):
        chart = tax_curve_chart([{"secondsHeld": 0, "taxBps": 500}])
        self.assertIsNotNone(chart)
        self.assertIn("M0.0", chart["line"])


class TestCurveValidation(unittest.TestCase):
    def test_presets_are_all_valid(self):
        for name, preset in PRESETS.items():
            with self.subTest(preset=name):
                self.assertIsNone(validate_tax_curve(preset["points"]))

    def test_mirrors_the_program_rules(self):
        self.assertIsNotNone(validate_tax_curve([]))
        self.assertIsNotNone(validate_tax_curve([{"secondsHeld": 60, "taxBps": 100}]))
        self.assertIsNotNone(
            validate_tax_curve(
                [{"secondsHeld": 0, "taxBps": 100}, {"secondsHeld": 60, "taxBps": 200}]
            ),
            "a rising curve must be rejected",
        )
        self.assertIsNotNone(validate_tax_curve([{"secondsHeld": 0, "taxBps": 9_001}]))
        self.assertIsNotNone(
            validate_tax_curve([{"secondsHeld": 0, "taxBps": 10}] * 9), "too many points"
        )

    def test_suggested_mints_are_unique_addresses(self):
        from stackapp_indexer.borsh import b58decode

        seen = {suggest_mint() for _ in range(20)}
        self.assertEqual(len(seen), 20)
        for mint in seen:
            self.assertEqual(len(b58decode(mint)), 32)


class TestEventRendering(unittest.TestCase):
    def test_every_event_kind_renders(self):
        samples = [
            ("BuyExecuted", {"buyer": "A" * 44, "amount": 1_000_000, "cost_lamports": 5}),
            ("ExitExecuted", {"owner": "B" * 44, "kind_name": "sell", "gross": 10, "tax": 3,
                              "top_tax_bps": 3_000}),
            ("ExitExecuted", {"owner": "B" * 44, "kind_name": "transfer", "gross": 10, "tax": 0,
                              "destination": "C" * 44}),
            ("TaxCollected", {"amount": 5, "undistributed": 5}),
            ("PoolClaimed", {"owner": "D" * 44, "amount": 7}),
            ("VestedClaimed", {"owner": "E" * 44, "released": 9}),
            ("TierUp", {"owner": "F" * 44, "new_tier": 3, "previous_tier": 2}),
            ("ReputationUpdated", {"owner": "G" * 44, "score_delta": 123,
                                   "capital_at_risk_lamports": 10**9}),
            ("LaunchInitialized", {"creator": "H" * 44, "opening_tax_bps": 3_000}),
            ("WeightSynced", {"previous_weight": 1, "new_weight": 2}),
            ("LotsCompacted", {"owner": "J" * 44, "lots_remaining": 3}),
        ]
        for name, data in samples:
            with self.subTest(event=name):
                html = str(render_event_html({"name": name, "data": data}))
                self.assertTrue(html.strip(), f"{name} rendered nothing")

    def test_unknown_event_degrades_gracefully(self):
        self.assertIn("Mystery", str(render_event_html({"name": "Mystery", "data": {}})))

    def test_a_matured_exit_says_so(self):
        html = str(
            render_event_html(
                {"name": "ExitExecuted", "data": {"owner": "A" * 44, "gross": 10, "tax": 0}}
            )
        )
        self.assertIn("no tax", html)

    def test_injected_markup_is_escaped(self):
        html = str(
            render_event_html(
                {"name": "ExitExecuted",
                 "data": {"owner": "<script>alert(1)</script>", "gross": 1, "tax": 0,
                          "kind_name": "<img onerror=x>"}}
            )
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;", html)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestRoutes(unittest.TestCase):
    """Exercise the real ASGI app, so a route that fails to register is caught."""

    @classmethod
    def setUpClass(cls):
        from stackapp_indexer.api import create_app

        cls.store = Store()
        cls.driver = MockDriver(cls.store, seed=11)
        cls.driver.bootstrap()
        # `mode="mock"` only changes the banner; routes are identical.
        cls.app = create_app(settings=Settings(), store=cls.store, mode="mock")
        cls.mint = cls.store.list_tokens()[0]["mint"]
        cls.owner = cls.store.positions_for_mint(cls.mint)[0]["owner"]

    def client(self):
        import httpx

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    def get(self, path):
        async def go():
            async with self.client() as client:
                return await client.get(path)

        return run(go())

    def test_every_page_renders(self):
        for path in ["/", "/launch", "/feed", f"/token/{self.mint}", f"/passport/{self.owner}"]:
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200, f"{path} -> {response.text[:200]}")
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn("StackApp", response.text)

    def test_pages_carry_the_prototype_warning(self):
        for path in ["/", "/launch", "/feed"]:
            with self.subTest(path=path):
                self.assertIn("Devnet prototype", self.get(path).text)

    def test_json_api_lives_under_api_and_is_not_shadowed(self):
        for path in ["/api/health", "/api/tokens", "/api/feed?limit=3",
                     f"/api/passport/{self.owner}", f"/api/tokens/{self.mint}"]:
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("application/json", response.headers["content-type"])

    def test_bare_paths_serve_html_not_json(self):
        # /feed and /passport/{w} exist in both worlds; the page must win.
        for path in ["/feed", f"/passport/{self.owner}"]:
            with self.subTest(path=path):
                self.assertIn("text/html", self.get(path).headers["content-type"])

    def test_unknown_mint_is_a_404(self):
        self.assertEqual(self.get("/token/" + "Z" * 44).status_code, 404)

    def test_unknown_wallet_gets_an_empty_passport_page(self):
        response = self.get("/passport/" + "Z" * 44)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Drifter", response.text)

    def test_static_assets_are_served(self):
        for path in ["/static/app.css", "/static/app.js"]:
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.content), 500)

    def test_feed_page_contains_rendered_events(self):
        text = self.get("/feed").text
        self.assertIn('class="feed"', text)
        self.assertIn("Live feed", text)

    def test_token_page_shows_the_tax_curve(self):
        text = self.get(f"/token/{self.mint}").text
        self.assertIn("Sell tax by time held", text)
        self.assertIn("<svg", text)

    def test_websocket_accepts_a_connection(self):
        """The /ws handshake must not 403 - see this module's docstring."""
        received = {}

        async def go():
            scope = {
                "type": "websocket",
                "path": "/ws",
                "raw_path": b"/ws",
                "headers": [],
                "query_string": b"",
                "client": ("test", 1),
                "server": ("test", 80),
                "subprotocols": [],
                "asgi": {"version": "3.0", "spec_version": "2.3"},
            }
            incoming = asyncio.Queue()
            await incoming.put({"type": "websocket.connect"})

            async def receive():
                return await incoming.get()

            async def send(message):
                received.setdefault("messages", []).append(message)
                if len(received["messages"]) >= 2:
                    raise asyncio.CancelledError

            with contextlib_suppress():
                await self.app(scope, receive, send)

        run(go())
        messages = received.get("messages", [])
        self.assertTrue(messages, "the app sent nothing at all")
        self.assertEqual(
            messages[0]["type"],
            "websocket.accept",
            f"handshake was rejected: {messages[0]}",
        )
        self.assertEqual(messages[1]["type"], "websocket.send")

    def test_render_event_endpoint(self):
        async def go():
            async with self.client() as client:
                return await client.post(
                    "/api/render-event",
                    json={"name": "BuyExecuted",
                          "data": {"buyer": "A" * 44, "amount": 1, "cost_lamports": 1,
                                   "timestamp": 1_700_000_000, "mint": self.mint}},
                )

        response = run(go())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("bought", body["html"])
        self.assertTrue(body["where"])

    def test_preview_endpoint_returns_rendered_html(self):
        async def go():
            async with self.client() as client:
                return await client.post(
                    "/api/preview", json={"points": CURVE, "vestSeconds": 604_800}
                )

        body = run(go()).json()
        self.assertIsNone(body["error"])
        self.assertIn("<svg", body["chartHtml"])
        self.assertIn("<table", body["tableHtml"])
        self.assertEqual(body["vestLabel"], "1w")
        self.assertEqual(body["openingLabel"], "30% tax")

    def test_preview_rejects_a_rising_curve(self):
        async def go():
            async with self.client() as client:
                return await client.post(
                    "/api/preview",
                    json={"points": [{"secondsHeld": 0, "taxBps": 100},
                                     {"secondsHeld": 60, "taxBps": 200}]},
                )

        self.assertIsNotNone(run(go()).json()["error"])

    def test_tx_endpoint_builds_an_unsigned_message(self):
        """The server must produce a message and never a signature."""
        from stackapp_indexer import txbuild

        async def fake_blockhash(_rpc):
            return "EETubP5AKHgjPAhzPAFcb8BAY1hMH639CWCFTqi3hq1k"

        original = txbuild.latest_blockhash
        txbuild.latest_blockhash = fake_blockhash
        try:

            async def go():
                async with self.client() as client:
                    return await client.post(
                        "/api/tx/buy",
                        json={"wallet": self.owner, "mint": self.mint, "amount": 1_000},
                    )

            response = run(go())
        finally:
            txbuild.latest_blockhash = original

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["action"], "buy")
        self.assertTrue(body["message"])
        self.assertNotIn("signature", body)
        self.assertNotIn("secret", response.text.lower())

    def test_tx_endpoint_rejects_junk(self):
        async def post(path, payload):
            async with self.client() as client:
                return await client.post(path, json=payload)

        self.assertEqual(run(post("/api/tx/nope", {"wallet": self.owner})).status_code, 404)
        self.assertEqual(run(post("/api/tx/buy", {"mint": self.mint})).status_code, 400)
        self.assertEqual(
            run(post("/api/tx/buy", {"wallet": "not-an-address", "mint": self.mint,
                                     "amount": 1})).status_code,
            400,
        )


class contextlib_suppress:
    """asyncio.CancelledError is how the fake `send` above stops the handler."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is asyncio.CancelledError


if __name__ == "__main__":
    unittest.main()
