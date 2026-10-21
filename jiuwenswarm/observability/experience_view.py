# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only view data layer for experience-memory visualization (Issue #12 / C4, slice 12a).

Pure-Python DTOs and builders that project the C1/C2/C3 stores
(:class:`EpisodeStore`, :class:`ExperienceStore`, :class:`ExperienceRetriever`)
into the shapes the visualization consumes:

- a session → episode → step tree (each episode's trajectory tasks/tools/members/
  handoffs become step nodes);
- experience rows with a per-experience injection count;
- injection attribution deduped by run (one row per ``(run_id, experience_id)``).

No RPC handler, no frontend: this is the data contract the later RPC handler
(slice 12b) and the ``ExperienceMemoryPanel`` (slice 12c) read. Reads only; it
never mutates a store.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jiuwenswarm.observability.episode_store import EpisodeStore
from jiuwenswarm.observability.experience_store import DEFAULT_SCOPE_LEVEL, DEFAULT_STATUS

StepKind: type[Literal] = Literal["task", "tool", "member", "handoff"]  # type: ignore[valid-type]


@dataclass(frozen=True, slots=True)
class EpisodeStepNode:
    """One step in an episode's projected trajectory.

    Attributes:
        label: The step's identifier (span id, tool name, subject, or handoff).
        kind: ``task`` (root span) / ``tool`` / ``member`` / ``handoff``.
        detail: A short human-readable note for the node.
    """

    label: str
    kind: StepKind
    detail: str = ""


@dataclass(frozen=True, slots=True)
class EpisodeTreeNode:
    """One episode in a session tree.

    Attributes:
        episode_id: The episode's stable id.
        turn_id: The turn within the session.
        objective: The episode's objective, if known.
        outcome: The episode's outcome, if known.
        steps: The projected trajectory steps (monotonic: only this episode's).
    """

    episode_id: str
    turn_id: str
    steps: tuple[EpisodeStepNode, ...]
    objective: str | None = None
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class SessionTree:
    """A session's episodes, each with its projected steps.

    Attributes:
        session_id: The session these episodes belong to.
        episodes: This session's episodes (monotonic containment: no other
            session's episodes surface here).
    """

    session_id: str
    episodes: tuple[EpisodeTreeNode, ...]


def build_session_tree(session_id: str, episode_store: EpisodeStore) -> SessionTree:
    """Build the session → episode → step tree for one session.

    Args:
        session_id: The session to project.
        episode_store: The episode store to read from (read-only).

    Returns:
        A ``SessionTree`` whose episodes are exactly ``session_id``'s, each
        carrying its own trajectory's steps. An unknown session yields an empty
        tree (no error).
    """
    episodes = episode_store.list_episodes(session_id)
    nodes: list[EpisodeTreeNode] = []
    for ep in episodes:
        trajectory = ep.trajectory
        steps: list[EpisodeStepNode] = []
        for task in trajectory.tasks:
            steps.append(EpisodeStepNode(label=task, kind="task", detail="root span"))
        for tool in trajectory.tools:
            steps.append(EpisodeStepNode(label=tool, kind="tool", detail="tool invoked"))
        for member in trajectory.members:
            steps.append(EpisodeStepNode(label=member, kind="member", detail="execution subject"))
        for handoff in trajectory.handoffs:
            steps.append(EpisodeStepNode(label=handoff, kind="handoff", detail="subject transition"))
        nodes.append(
            EpisodeTreeNode(
                episode_id=ep.id,
                turn_id=ep.turn_id,
                steps=tuple(steps),
                objective=ep.objective,
                outcome=ep.outcome,
            )
        )
    return SessionTree(session_id=session_id, episodes=tuple(nodes))


@dataclass(frozen=True, slots=True)
class ExperienceRow:
    """One experience row for the experience tab, with its injection count.

    Attributes:
        id / title / content / scope_level / status: The experience's fields.
        evidence_count / confidence / use_count: Quality and attribution counters.
        source_episode_ids / target_hints: Provenance and targeting.
        injection_count: How many distinct runs injected this experience.
    """

    id: str
    title: str
    content: str
    scope_level: str
    status: str
    evidence_count: int
    confidence: float | None
    use_count: int
    source_episode_ids: tuple[str, ...]
    target_hints: tuple[str, ...]
    injection_count: int


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path), check_same_thread=False)


def build_experience_rows(experiences_db_path: Path) -> list[ExperienceRow]:
    """List all experiences with their injection counts, newest first.

    Args:
        experiences_db_path: Path to ``experiences.db`` (shared with the
            retriever's ``injection_records`` table).

    Returns:
        One ``ExperienceRow`` per experience; ``injection_count`` is the number
        of distinct runs that injected it (0 when never injected). An absent
        database yields an empty list.
    """
    if not Path(experiences_db_path).exists():
        return []
    conn = _connect(experiences_db_path)
    try:
        rows = conn.execute(
            """
            SELECT e.id, e.title, e.content, e.scope_level, e.status,
                   e.evidence_count, e.confidence, e.use_count,
                   e.source_episode_ids, e.target_hints,
                   COUNT(i.id) AS injection_count
            FROM experiences e
            LEFT JOIN injection_records i ON i.experience_id = e.id
            GROUP BY e.id
            ORDER BY e.created_at DESC, e.id ASC
            """
        ).fetchall()
    finally:
        conn.close()
    out: list[ExperienceRow] = []
    for r in rows:
        source_raw = r[8]
        hints_raw = r[9]
        out.append(
            ExperienceRow(
                id=str(r[0]),
                title=str(r[1]),
                content=str(r[2]),
                scope_level=str(r[3] or DEFAULT_SCOPE_LEVEL),
                status=str(r[4] or DEFAULT_STATUS),
                evidence_count=int(r[5] or 0),
                confidence=r[6],
                use_count=int(r[7] or 0),
                source_episode_ids=tuple(json.loads(source_raw)) if source_raw else (),
                target_hints=tuple(json.loads(hints_raw)) if hints_raw else (),
                injection_count=int(r[10] or 0),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class InjectionAttribution:
    """How many distinct runs injected one experience.

    Attributes:
        experience_id: The experience that was injected.
        experience_title: Its title (for display).
        injection_count: Distinct runs that injected it (deduped by run_id).
        runs: The distinct run ids, sorted.
    """

    experience_id: str
    experience_title: str
    injection_count: int
    runs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttributionStats:
    """Aggregate injection attribution across all experiences.

    Attributes:
        total_experiences: Number of experiences with at least one injection.
        total_injections: Total distinct (run, experience) injections.
        per_experience: One ``InjectionAttribution`` per injected experience.
    """

    total_experiences: int
    total_injections: int
    per_experience: tuple[InjectionAttribution, ...]


def build_attribution(experiences_db_path: Path) -> AttributionStats:
    """Aggregate injection attribution, deduped by run.

    Each ``(run_id, experience_id)`` row in ``injection_records`` is one
    injection (the table's ``UNIQUE`` constraint already dedups repeats), so
    ``injection_count`` per experience is the number of distinct runs that
    injected it.

    Args:
        experiences_db_path: Path to ``experiences.db``.

    Returns:
        Aggregate attribution stats. An absent database yields zero stats.
    """
    if not Path(experiences_db_path).exists():
        return AttributionStats(total_experiences=0, total_injections=0, per_experience=())
    conn = _connect(experiences_db_path)
    try:
        rows = conn.execute(
            """
            SELECT i.experience_id,
                   COALESCE(e.title, e.content, i.experience_id) AS title,
                   COUNT(DISTINCT i.run_id) AS cnt,
                   GROUP_CONCAT(DISTINCT i.run_id) AS runs
            FROM injection_records i
            LEFT JOIN experiences e ON e.id = i.experience_id
            GROUP BY i.experience_id
            ORDER BY cnt DESC, i.experience_id ASC
            """
        ).fetchall()
    finally:
        conn.close()
    per: list[InjectionAttribution] = []
    total = 0
    for r in rows:
        runs_csv = r[3] or ""
        runs = tuple(sorted({x for x in runs_csv.split(",") if x})) if runs_csv else ()
        count = int(r[2] or 0)
        total += count
        per.append(
            InjectionAttribution(
                experience_id=str(r[0]),
                experience_title=str(r[1]),
                injection_count=count,
                runs=runs,
            )
        )
    return AttributionStats(
        total_experiences=len(per),
        total_injections=total,
        per_experience=tuple(per),
    )


__all__ = [
    "AttributionStats",
    "EpisodeStepNode",
    "EpisodeTreeNode",
    "ExperienceRow",
    "InjectionAttribution",
    "SessionTree",
    "build_attribution",
    "build_experience_rows",
    "build_session_tree",
]
