"""StackApp indexer.

Subscribes to the program's logs and account changes on devnet, decodes Anchor
events, and serves a queryable view over REST + WebSocket.

The derived numbers the UI shows (vesting %, current tax rate, projected pool
share) are computed with `stackapp_sim` - the same Python mirror of the
on-chain math the test suite uses - so the dashboard and the program cannot
quietly disagree. If `stackapp_sim` is not installed, the sibling `sim/`
directory in this repo is added to `sys.path`, so a fresh checkout runs with no
install step.
"""

from __future__ import annotations

import pathlib
import sys

try:  # pragma: no cover - import-time plumbing
    import stackapp_sim  # noqa: F401
except ImportError:  # pragma: no cover
    _sim = pathlib.Path(__file__).resolve().parents[2] / "sim"
    if _sim.is_dir():
        sys.path.insert(0, str(_sim))
    else:  # pragma: no cover
        raise

__version__ = "0.1.0"

__all__ = ["__version__"]
