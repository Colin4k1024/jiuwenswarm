# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Offline experience-pipeline visualization (Issue #13 / C5).

Generates a self-contained HTML file depicting the experience-memory evolution
pipeline as a force-directed graph (vis.js) with the canonical 7-stage path
highlighted:

    signal → candidate → episode → evaluation → release → canary → experience

Node labels carry live counts from the C2/C3 stores when an ``AttributionStats``
is supplied; with no data the stages still render (empty state). The output is
an offline artifact for docs/demos — not a runtime UI (that is the C4 panel).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenswarm.observability.experience_view import AttributionStats

# The canonical evolution pipeline. The order is part of the contract: a later
# stage never feeds an earlier one.
STAGES: tuple[str, ...] = (
    "signal",
    "candidate",
    "episode",
    "evaluation",
    "release",
    "canary",
    "experience",
)


def _stage_counts(stats: "AttributionStats | None") -> dict[str, int]:
    """Map pipeline stages to live counts, where the stores expose them."""
    if stats is None:
        return {}
    return {
        # Only ``experience`` carries a directly-comparable count today; the
        # earlier stages are surfaced as zero so the shape is stable.
        "experience": stats.total_experiences,
    }


def build_experience_pipeline_html(stats: "AttributionStats | None" = None) -> str:
    """Return a self-contained HTML document for the experience pipeline.

    Args:
        stats: Optional attribution stats; when supplied, the ``experience``
            node label carries the live experience count.

    Returns:
        A complete HTML string embedding a vis.js network with the 7 pipeline
        stages and the highlighted monotonic path between them.
    """
    counts = _stage_counts(stats)
    nodes_js = ", ".join(
        '{id: "%s", label: "%s%s"}'
        % (stage, stage, f"\\n{counts[stage]}" if stage in counts else "")
        for stage in STAGES
    )
    # Highlighted monotonic path: each stage feeds only the next.
    edges_js = ", ".join(
        '{from: "%s", to: "%s", arrows: "to", color: {color: "#d93f0b", highlight: "#d93f0b"}}'
        % (STAGES[i], STAGES[i + 1])
        for i in range(len(STAGES) - 1)
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Experience Memory Evolution Pipeline</title>
<script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  #pipeline {{ width: 100%; height: 600px; border: 1px solid #ccc; }}
  body {{ font-family: sans-serif; margin: 16px; }}
</style>
</head>
<body>
<h1>Experience Memory Evolution Pipeline</h1>
<p>The canonical 7-stage path (highlighted): signal &rarr; candidate &rarr;
episode &rarr; evaluation &rarr; release &rarr; canary &rarr; experience.</p>
<div id="pipeline"></div>
<script type="text/javascript">
  var nodes = new vis.DataSet([{nodes_js}]);
  var edges = new vis.DataSet([{edges_js}]);
  var container = document.getElementById("pipeline");
  var data = {{ nodes: nodes, edges: edges }};
  var options = {{ nodes: {{ shape: "box" }}, edges: {{ arrows: {{ to: true }} }} }};
  new vis.Network(container, data, options);
</script>
</body>
</html>
"""


__all__ = ["STAGES", "build_experience_pipeline_html"]
