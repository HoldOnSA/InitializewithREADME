"""Guard against the Python mirror drifting from the Anchor program.

`stackapp_sim` only proves anything about the deployed program if the two
agree on their constants. This parses the Rust source and compares.
"""

import ast
import operator
import pathlib
import re
import unittest

from stackapp_sim import constants as py

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSTANTS_RS = REPO / "programs" / "stackapp" / "src" / "constants.rs"
LIB_RS = REPO / "programs" / "stackapp" / "src" / "lib.rs"
REGISTRATION_RS = REPO / "programs" / "stackapp" / "src" / "state" / "registration.rs"
DEPOSIT_VAULT_RS = REPO / "programs" / "stackapp" / "src" / "state" / "deposit_vault.rs"

# Byte size of every field type these two structs actually use. Rust computes
# rent-exemption dynamically via `Rent::get()?.minimum_balance(space)`, so
# there's no `pub const` for `space` itself to scrape - this reconstructs it
# from the struct's real field list instead.
_RUST_FIELD_SIZES = {
    "Pubkey": 32,
    "u8": 1,
    "u16": 2,
    "u32": 4,
    "u64": 8,
    "u128": 16,
    "i8": 1,
    "i16": 2,
    "i32": 4,
    "i64": 8,
    "i128": 16,
    "bool": 1,
}


def rust_struct_account_len(path: pathlib.Path, struct_name: str) -> int:
    """8-byte Anchor discriminator + the sum of a `#[account] pub struct
    Name { ... }`'s real field sizes, parsed straight from the Rust source."""
    source = path.read_text(encoding="utf-8")
    match = re.search(rf"pub struct {struct_name}\s*\{{(.*?)\}}", source, re.DOTALL)
    if not match:
        raise AssertionError(f"struct {struct_name} not found in {path}")
    body = re.sub(r"//.*", "", match.group(1))  # strip line comments
    fields = re.findall(r"pub \w+\s*:\s*(\w+)\s*,", body)
    if not fields:
        raise AssertionError(f"no fields parsed for struct {struct_name} in {path}")
    return 8 + sum(_RUST_FIELD_SIZES[f] for f in fields)

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
}


def _eval_int(expr: str) -> int:
    """Evaluate a Rust integer literal expression like `1 + 1_000_000`."""
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
        raw = re.sub(r"//.*", "", raw)
        try:
            out[name] = _eval_int(raw)
        except (ValueError, SyntaxError):
            continue  # non-numeric consts (seeds are byte strings)
    return out


def rust_scalar_array(source: str, name: str) -> list:
    match = re.search(rf"pub const {name}\s*:\s*\[[^\]]+\]\s*=\s*\[(.*?)\];", source, re.DOTALL)
    if not match:
        raise AssertionError(f"{name} not found in constants.rs")
    body = re.sub(r"//.*", "", match.group(1))
    return [_eval_int(p) for p in body.split(",") if p.strip()]


class TestRustPythonParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.constants_src = CONSTANTS_RS.read_text(encoding="utf-8")
        cls.scalars = rust_scalars(cls.constants_src)

    def test_the_rust_sources_are_where_we_expect(self):
        self.assertTrue(CONSTANTS_RS.is_file(), f"missing {CONSTANTS_RS}")
        self.assertTrue(LIB_RS.is_file(), f"missing {LIB_RS}")
        self.assertTrue(REGISTRATION_RS.is_file(), f"missing {REGISTRATION_RS}")
        self.assertTrue(DEPOSIT_VAULT_RS.is_file(), f"missing {DEPOSIT_VAULT_RS}")

    def test_scalar_constants_match(self):
        shared = [
            "BPS_DENOMINATOR",
            "ACC_PRECISION",
            "MIN_CLAIM_DELAY_SLOTS",
            "REGISTRATION_MARKER_LAMPORTS",
        ]
        for name in shared:
            with self.subTest(constant=name):
                self.assertIn(name, self.scalars, f"{name} not found in the Rust source")
                self.assertEqual(
                    getattr(py, name),
                    self.scalars[name],
                    f"{name} differs between Rust and Python",
                )

    def test_tenure_tier_seconds_match(self):
        self.assertEqual(
            list(py.TENURE_TIER_SECONDS),
            rust_scalar_array(self.constants_src, "TENURE_TIER_SECONDS"),
        )

    def test_tenure_tier_multiplier_bps_match(self):
        self.assertEqual(
            list(py.TENURE_TIER_MULTIPLIER_BPS),
            rust_scalar_array(self.constants_src, "TENURE_TIER_MULTIPLIER_BPS"),
        )

    def test_pda_seeds_match(self):
        for py_name, seed in [
            ("SEED_GLOBAL_CONFIG", py.SEED_GLOBAL_CONFIG),
            ("SEED_CONFIG", py.SEED_CONFIG),
            ("SEED_POOL", py.SEED_POOL),
            ("SEED_DEPOSIT_VAULT", py.SEED_DEPOSIT_VAULT),
            ("SEED_REGISTRATION", py.SEED_REGISTRATION),
        ]:
            with self.subTest(seed=py_name):
                expected = seed.decode()
                self.assertRegex(
                    self.constants_src,
                    rf'pub const {py_name}\s*:\s*&\[u8\]\s*=\s*b"{expected}";',
                    f"{py_name} differs between Rust and Python",
                )

    def test_registration_account_length_matches_the_real_struct(self):
        """`REGISTRATION_RENT_LAMPORTS` is derived from a hand-maintained
        `REGISTRATION_ACCOUNT_LEN` byte count - this is what catches it
        silently going stale if a field is ever added to, removed from, or
        retyped in the real `Registration` struct. Rust itself never has this
        problem: it computes rent dynamically via `Rent::get()?.minimum_balance()`
        against the struct's real, compiler-known size, not a hand-written
        constant."""
        self.assertEqual(
            py.REGISTRATION_ACCOUNT_LEN,
            rust_struct_account_len(REGISTRATION_RS, "Registration"),
        )

    def test_deposit_vault_account_length_matches_the_real_struct(self):
        self.assertEqual(
            py.DEPOSIT_VAULT_ACCOUNT_LEN,
            rust_struct_account_len(DEPOSIT_VAULT_RS, "DepositVault"),
        )

    def test_tenure_multiplier_is_bounded(self):
        """A super-linear tenure weight would let the earliest wallet capture
        the pool permanently. Keep the cap explicit and checked."""
        multipliers = list(py.TENURE_TIER_MULTIPLIER_BPS)
        self.assertEqual(multipliers[0], py.BPS_DENOMINATOR, "tier 0 must be 1.00x")
        self.assertEqual(multipliers, sorted(multipliers), "tiers must not go backwards")
        self.assertLessEqual(max(multipliers), 3 * py.BPS_DENOMINATOR, "cap is 3x")

    def test_every_instruction_has_a_handler(self):
        """The `#[program]` module and `instructions/` must not drift apart."""
        lib_rs = LIB_RS.read_text(encoding="utf-8")
        declared = set(re.findall(r"pub fn (\w+)\s*\(\s*ctx:", lib_rs))
        expected = {
            "initialize_config",
            "update_authority",
            "register_mint",
            "write_registration",
            "sync",
            "claim",
            "donate",
            "reconcile",
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
