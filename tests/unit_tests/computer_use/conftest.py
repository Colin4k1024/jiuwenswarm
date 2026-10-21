"""Pytest path setup for the computer_use package.

The cua_engine is a standalone package (``computer-use-toolkit``) whose source
tree lives at ``jiuwenswarm/cua_engine/src``. It is not declared as a dependency
of the root ``workswarm`` distribution, so the importable ``computer_use``
package is not on ``sys.path`` by default. The inline ``Path(__file__)``
expression below is statically verified by ``test_dependency_provenance`` to
resolve inside the repository (mirroring ``tests/unit_tests/test_agentos_policy.py``);
an intermediate variable would defeat that check.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[3] / "jiuwenswarm" / "cua_engine" / "src"),
)
