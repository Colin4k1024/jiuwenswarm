"""Unit tests for the experience-pipeline visualization (Issue #13 / C5)."""

from __future__ import annotations

from pathlib import Path

from jiuwenswarm.observability.experience_pipeline_viz import (
    STAGES,
    build_experience_pipeline_html,
)
from jiuwenswarm.observability.experience_view import AttributionStats, InjectionAttribution


def test_pipeline_html_contains_seven_stages() -> None:
    html = build_experience_pipeline_html()
    assert "<html" in html.lower()
    for stage in STAGES:
        assert stage in html
    assert len(STAGES) == 7


def test_pipeline_html_path_is_monotonic() -> None:
    html = build_experience_pipeline_html()
    # Each consecutive stage pair appears as a highlighted edge, in order.
    for i in range(len(STAGES) - 1):
        edge = f'from: "{STAGES[i]}", to: "{STAGES[i + 1]}"'
        assert edge in html, f"missing monotonic edge {edge!r}"


def test_pipeline_html_empty_state_is_valid() -> None:
    html = build_experience_pipeline_html(stats=None)
    assert "<html" in html.lower()
    assert "experience" in html  # stages render even with no data


def test_pipeline_html_carries_live_experience_count() -> None:
    stats = AttributionStats(
        total_experiences=3,
        total_injections=5,
        per_experience=(
            InjectionAttribution(
                experience_id="exp-1",
                experience_title="use target",
                injection_count=2,
                runs=("r1", "r2"),
            ),
        ),
    )
    html = build_experience_pipeline_html(stats=stats)
    # The live count lands on the experience node label.
    assert "\\n3" in html
