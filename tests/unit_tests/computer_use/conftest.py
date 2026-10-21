"""Pytest path setup for the computer_use package.

The cua_engine is a standalone package (``computer-use-toolkit``) whose source
tree lives at ``jiuwenswarm/cua_engine/src``. It is not declared as a dependency
of the root ``workswarm`` distribution, so the importable ``computer_use``
package is not on ``sys.path`` by default. Tests in this directory import the
engine directly; insert its source root once at collection time.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ENGINE_SRC = (
    Path(__file__).resolve().parents[3] / "jiuwenswarm" / "cua_engine" / "src"
)
if _ENGINE_SRC.is_dir() and str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))
