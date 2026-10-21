#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generate the experience-pipeline visualization HTML.

Reads the shared ``experiences.db`` under the trace root and writes a
self-contained ``experience_pipeline.html`` to ``docs/viz/`` (created on first
use). The artifact is gitignored for size; only this generator is kept.

Usage::

    python scripts/viz_experience_pipeline.py [output_path]
"""

from __future__ import annotations

import sys
from pathlib import Path

from jiuwenswarm.observability.experience_view import (
    AttributionStats,
    build_attribution,
)
from jiuwenswarm.observability.experience_pipeline_viz import (
    build_experience_pipeline_html,
)
from jiuwenswarm.observability.experience_store import experiences_database_path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = Path(argv[0]) if argv else Path("docs/viz/experience_pipeline.html")
    stats: AttributionStats | None
    try:
        stats = build_attribution(experiences_database_path())
    except Exception:
        # No store yet → render the empty-state pipeline.
        stats = None
    html = build_experience_pipeline_html(stats)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
