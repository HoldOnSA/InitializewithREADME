"""Guard against the Python mirror drifting from the Anchor program.

`stackapp_sim` only proves anything about the deployed program if the two agree
on their constants. This parses the Rust source and compares.
"""

import ast
import operator
import pathlib
import re
import unittest

from stackapp_sim import constants as py

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSTANTS_RS = REPO / "programs" / "stackapp" / "src" / "constants.rs"
LAUNCH_RS = REPO / "programs" / "stackapp" / "src" / "instructions" / "initialize_launch.rs"

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
}


def _eval_int(expr: str) -> int:
    """Evaluate a Rust integer literal expression like `60 * 60 * 24 * 365 * 2`."""
    cleaned = expr.replace("_", "").strip()
    node = ast.parse(cleaned, mode="eval").body

    def walk(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _SAFE_OPS:
            return _SAFE_OPS[type(n.op)](walk(n.left), walk(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -walk(n.operand)
        raise ValueError(f"unsupported expression: {expr!r}")

    return walk(node)


def rust_scalars(source: str) -> dict:
    """`pub const NAME: type = <expr>;` -> {NAME: int}, skipping arrays/slices."""
    out = {}
    pattern = re.compile(
        r"pub const (\w+)\s*:\s*[\w:]+\s*=\s*([^;\[]+?);",
        re.MULTILINE,
    )
    for name, raw in pattern.findall(source):
        try:
            out[name] = _eval_int(raw)
        except (ValueError, SyntaxError):
            continue  # non-numeric consts (seeds are byte strings)
    return out


def rust_tuple_array(source: str, name: str) -> list:
    """Parse `pub const NAME: [(i64, u64); N] = [ (a, b), ... ];`"""
    match = re.search(
        rf"pub const {name}\s*:\s*\[[^\]]+\]\s*=\s*\[(.*?)\];", source, re.DOTALL
    )
    if not match:
        raise AssertionError(f"{name} not found in constants.rs")
    body = re.sub(r"//.*", "", match.group(1))
    return [
        (_eval_int(a), _eval_int(b))
        for a, b in re.findall(r"\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)", body)
    ]


def rust_scalar_array(source: str, name: str) -> list:
    match = re.search(
        rf"pub const {name}\s*:\s*\[[^\]]+\]\s*=\s*\[(.*?)\];", source, re.DOTALL
    )
    if not match:
        raise AssertionError(f"{name} not found in constants.rs")
    body = re.sub(r"//.*", "", match.group(1))
    return [_eval_int(p) for p in body.split(",") if p.strip()]


class TestRustPythonParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.constants_src = CONSTANTS_RS.read_text(encoding="utf-8")
        cls.launch_src = LAUNCH_RS.read_text(encoding="utf-8")
        cls.scalars = rust_scalars(cls.constants_src)
        cls.scalars.update(rust_scalars(cls.launch_src))

    def test_the_rust_sources_are_where_we_expect(self):
        self.assertTrue(CONSTANTS_RS.is_file(), f"missing {CONSTANTS_RS}")
        self.assertTrue(LAUNCH_RS.is_file(), f"missing {LAUNCH_RS}")

    def test_scalar_constants_match(self):
        shared = [
            "BPS_DENOMINATOR",
            "ACC_PRECISION",
            "MAX_TAX_CURVE_POINTS",
            "MAX_LOTS",
            "MAX_TAX_BPS",
            "MIN_CLAIM_DELAY_SLOTS",
            "MAX_VEST_DURATION_SECONDS",
            "MATURITY_MIN_RETENTION_BPS",
            "REP_SCORE_DIVISOR",
            "MAX_REPUTATION_TIER",
            "DEFAULT_VIRTUAL_SOL_RESERVES",
            "DEFAULT_VIRTUAL_TOKEN_RESERVES",
        ]
        for name in shared:
            with self.subTest(constant=name):
                self.assertIn(name, self.scalars, f"{name} not found in the Rust source")
                self.assertEqual(
                    getattr(py, name),
                    self.scalars[name],
                    f"{name} differs between Rust and Python",
                )

    def test_tenure_tiers_match(self):
        self.assertEqual(
            list(py.TENURE_TIERS), rust_tuple_array(self.constants_src, "TENURE_TIERS")
        )

    def test_reputation_thresholds_match(self):
        self.assertEqual(
            list(py.REPUTATION_TIER_THRESHOLDS),
            rust_scalar_array(self.constants_src, "REPUTATION_TIER_THRESHOLDS"),
        )

    def test_pda_seeds_match(self):
        for py_name, seed in [
            ("SEED_CONFIG", py.SEED_CONFIG),
            ("SEED_POOL", py.SEED_POOL),
            ("SEED_POSITION", py.SEED_POSITION),
            ("SEED_REPUTATION", py.SEED_REPUTATION),
            ("SEED_CURVE_VAULT", py.SEED_CURVE_VAULT),
        ]:
            with self.subTest(seed=py_name):
                expected = seed.decode()
                self.assertRegex(
                    self.constants_src,
                    rf'pub const {py_name}\s*:\s*&\[u8\]\s*=\s*b"{expected}";',
                    f"{py_name} differs between Rust and Python",
                )

    def test_tenure_multiplier_is_bounded(self):
        """A super-linear tenure weight would let the earliest wallet capture
        the pool permanently. Keep the cap explicit and checked."""
        multipliers = [m for _, m in py.TENURE_TIERS]
        self.assertEqual(multipliers[0], py.BPS_DENOMINATOR, "tier 0 must be 1.00x")
        self.assertEqual(multipliers, sorted(multipliers), "tiers must not go backwards")
        self.assertLessEqual(max(multipliers), 3 * py.BPS_DENOMINATOR, "cap is 3x")

    def test_every_instruction_has_a_handler(self):
        """The `#[program]` module and `instructions/` must not drift apart."""
        lib_rs = (REPO / "programs" / "stackapp" / "src" / "lib.rs").read_text(encoding="utf-8")
        declared = set(re.findall(r"pub fn (\w+)\s*\(\s*ctx:", lib_rs))
        expected = {
            "initialize_launch",
            "buy",
            "claim_vested",
            "sell",
            "transfer_position",
            "donate_to_pool",
            "claim_pool_share",
            "update_reputation",
            "sync_weight",
            "compact_lots",
        }
        self.assertEqual(declared, expected)

        instructions_dir = REPO / "programs" / "stackapp" / "src" / "instructions"
        for name in expected:
            with self.subTest(instruction=name):
                path = instructions_dir / f"{name}.rs"
                self.assertTrue(path.is_file(), f"missing handler file {path}")
                self.assertIn("pub fn handler", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
