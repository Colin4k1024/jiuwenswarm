# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cross-session SQLite store for structurally-projected episodes.

The trajectory store under :mod:`jiuwenswarm.observability.store` persists raw
OTLP spans, one SQLite file per session. An *episode* is the next layer up:
one model turn (a ``request_id``) within a session, projected from its spans
into a structured ``EpisodeTrajectory`` (tasks / tools / members / handoffs).
Episodes are cross-session — they feed the experience-memory pipeline
(C2 ExperienceStore, C3 Retriever) — so they live in a single shared
``episodes.db`` under the trace root, not in the per-session trajectory files.

This module is additive: it owns its own ``CREATE TABLE IF NOT EXISTS`` and does
not touch the trajectory store's schema-version migration. Writes are
synchronous (stdlib :mod:`sqlite3`) because episodes land once per turn at
finalization, not on a hot stream.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The four-level scope the experience-memory pipeline (C2/C3) keys recall on.
# Episodes default to ``user`` scope: a single user's own turns, promotable to
# project/tenant/global by later policy.
DEFAULT_SCOPE_LEVEL = "user"

# Column order is part of the row contract; keep insert/select aligned.
_EPISODE_COLUMNS = (
    "id",
    "session_id",
    "turn_id",
    "execution_subject_id",
    "objective",
    "input_summary",
    "expected_outcome",
    "run_snapshot_id",
    "version_refs",
    "trajectory",
    "outcome",
    "human_feedback",
    "metrics",
    "attribution",
    "root_cause",
    "confidence",
    "scope_level",
    "created_at",
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    execution_subject_id TEXT NOT NULL DEFAULT 'main',
    objective TEXT,
    input_summary TEXT,
    expected_outcome TEXT,
    run_snapshot_id TEXT,
    version_refs TEXT,
    trajectory TEXT NOT NULL,
    outcome TEXT,
    human_feedback TEXT,
    metrics TEXT,
    attribution TEXT,
    root_cause TEXT,
    confidence REAL,
    scope_level TEXT NOT NULL DEFAULT 'user',
    created_at INTEGER NOT NULL,
    UNIQUE(session_id, turn_id, execution_subject_id)
);
CREATE INDEX IF NOT EXISTS idx_episodes_session_created
    ON episodes(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_episodes_scope_created
    ON episodes(scope_level, created_at DESC);
"""


@dataclass(frozen=True, slots=True)
class EpisodeTrajectory:
    """Structured projection of one episode's spans.

    Attributes:
        tasks: Root span identifiers in the turn (spans whose parent is absent
            from the turn's span set).
        tools: Distinct tool names invoked during the turn.
        members: Distinct execution subjects that owned spans in the turn.
        handoffs: ``"<parent_subject>-><child_subject>"`` transitions recorded
            where a child span's subject differs from its parent's.
    """

    tasks: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    members: tuple[str, ...] = ()
    handoffs: tuple[str, ...] = ()

    def to_json(self) -> str:
        """Serialize to a canonical JSON string."""
        return json.dumps(
            {
                "tasks": list(self.tasks),
                "tools": list(self.tools),
                "members": list(self.members),
                "handoffs": list(self.handoffs),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> "EpisodeTrajectory":
        """Deserialize from :meth:`to_json` output.

        Args:
            raw: The JSON string produced by :meth:`to_json`.

        Returns:
            An ``EpisodeTrajectory`` equal to the one serialized.
        """
        payload = json.loads(raw)
        return cls(
            tasks=tuple(payload.get("tasks") or ()),
            tools=tuple(payload.get("tools") or ()),
            members=tuple(payload.get("members") or ()),
            handoffs=tuple(payload.get("handoffs") or ()),
        )

    def is_empty(self) -> bool:
        """Return True when the projection carries no activity at all."""
        return not (self.tasks or self.tools or self.members or self.handoffs)


def project_trajectory(spans: Sequence[Mapping[str, Any]]) -> EpisodeTrajectory:
    """Project a turn's spans into a structured trajectory.

    The input is the spans of one ``(session_id, turn_id)`` slice, already
    filtered by the caller. A span is a mapping with string-valued
    ``span_id`` / ``parent_span_id`` / ``tool_name`` / ``execution_subject_id``
    keys (any may be absent or ``None``).

    Args:
        spans: Spans belonging to exactly one turn.

    Returns:
        The projected trajectory. An empty input yields an empty trajectory,
        which the store rejects on create (no episode for an empty turn).
    """
    span_ids: set[str] = set()
    subject_by_span: dict[str, str] = {}
    for span in spans:
        sid = _str_or_none(span.get("span_id"))
        if sid:
            span_ids.add(sid)
            subj = _str_or_none(span.get("execution_subject_id")) or "main"
            subject_by_span[sid] = subj

    tools: set[str] = set()
    members: set[str] = set()
    tasks: set[str] = set()
    handoffs: set[str] = set()
    for span in spans:
        sid = _str_or_none(span.get("span_id"))
        if not sid:
            continue
        subj = subject_by_span.get(sid, "main")
        if subj:
            members.add(subj)
        tool = _str_or_none(span.get("tool_name"))
        if tool:
            tools.add(tool)
        parent = _str_or_none(span.get("parent_span_id"))
        if not parent or parent not in span_ids:
            tasks.add(sid)
        elif parent in subject_by_span:
            parent_subj = subject_by_span[parent]
            if parent_subj and parent_subj != subj:
                handoffs.add(f"{parent_subj}->{subj}")

    return EpisodeTrajectory(
        tasks=tuple(sorted(tasks)),
        tools=tuple(sorted(tools)),
        members=tuple(sorted(members)),
        handoffs=tuple(sorted(handoffs)),
    )


@dataclass(frozen=True, slots=True)
class Episode:
    """One persisted episode row.

    Attributes:
        id: Server-assigned stable identifier (UUID). Empty until persisted.
        session_id: Owning session identifier.
        turn_id: The turn within the session (maps to OTLP ``request_id``).
        execution_subject_id: The subject that owned this turn's chain.
        objective: Human-readable turn objective, if known.
        input_summary: Condensed input the turn acted on.
        expected_outcome: What the turn set out to achieve.
        run_snapshot_id: Reference to a run snapshot for replay.
        version_refs: Code/version references the turn touched.
        trajectory: The structured span projection (required).
        outcome: ``success`` / ``failure`` / ``partial`` / ... (free-form).
        human_feedback: Structured feedback attached to the episode.
        metrics: Numeric measurements (latency, token cost, ...).
        attribution: Who/what produced the outcome.
        root_cause: Failure root cause when known.
        confidence: Confidence in the outcome, in ``[0, 1]``.
        scope_level: Memory scope (``user`` / ``project`` / ``tenant`` / ``global``).
        created_at: Unix-millisecond ingestion timestamp. Zero until persisted.
    """

    session_id: str
    turn_id: str
    trajectory: EpisodeTrajectory
    id: str = ""
    execution_subject_id: str = "main"
    objective: str | None = None
    input_summary: str | None = None
    expected_outcome: str | None = None
    run_snapshot_id: str | None = None
    version_refs: tuple[str, ...] = ()
    outcome: str | None = None
    human_feedback: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    attribution: Mapping[str, Any] = field(default_factory=dict)
    root_cause: str | None = None
    confidence: float | None = None
    scope_level: str = DEFAULT_SCOPE_LEVEL
    created_at: int = 0


def episodes_database_path(workspace: Path | None = None) -> Path:
    """Return the shared episodes database path under the trace root.

    Args:
        workspace: Optional data root override (for isolated tests). When
            ``None``, the real user workspace is resolved lazily so importing
            this module never pulls the full config chain.

    Returns:
        ``<workspace>/.trace/episodes.db``.
    """
    if workspace is None:
        from jiuwenswarm.common.utils import get_user_workspace_dir

        workspace = get_user_workspace_dir()
    return Path(workspace) / ".trace" / "episodes.db"


class EpisodeStore:
    """Synchronous SQLite store for episodes.

    The store owns a single shared ``episodes.db``. ``ensure`` is idempotent and
    additive; it does not participate in the trajectory store's schema-version
    migration.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # -- connection -----------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # WAL keeps concurrent readers (C3 retriever) unblocked while a turn
        # finalizes its episode.
        conn = sqlite3.connect(
            self._db_path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        return conn

    def ensure(self) -> None:
        """Create the episodes table and indexes if they do not yet exist."""
        conn = self._connect()
        conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        """Close the underlying connection. Safe to call once."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- CRUD -----------------------------------------------------------

    def create_episode(self, episode: Episode) -> Episode:
        """Persist one episode, upserting on its unique turn key.

        The unique key is ``(session_id, turn_id, execution_subject_id)``:
        re-finalizing a turn updates the row in place rather than duplicating.

        Args:
            episode: The episode to persist. ``id`` and ``created_at`` are
                assigned by the store when unset.

        Returns:
            The persisted episode with ``id`` and ``created_at`` filled in.

        Raises:
            ValueError: If the trajectory is empty (no activity → no episode).
        """
        if episode.trajectory.is_empty():
            raise ValueError(
                "refusing to persist an episode with an empty trajectory "
                "(no tasks/tools/members/handoffs)"
            )
        conn = self._connect()
        # Millisecond resolution so two turns finalized in the same second keep
        # their insertion order under the (created_at, id) tiebreaker.
        now = int(time.time() * 1000)
        existing = conn.execute(
            "SELECT id, created_at FROM episodes "
            "WHERE session_id = ? AND turn_id = ? AND execution_subject_id = ?",
            (episode.session_id, episode.turn_id, episode.execution_subject_id),
        ).fetchone()
        if existing:
            row_id, created_at = existing
        else:
            row_id = episode.id or str(uuid.uuid4())
            created_at = episode.created_at or now
        persisted = Episode(
            id=row_id,
            session_id=episode.session_id,
            turn_id=episode.turn_id,
            execution_subject_id=episode.execution_subject_id,
            objective=episode.objective,
            input_summary=episode.input_summary,
            expected_outcome=episode.expected_outcome,
            run_snapshot_id=episode.run_snapshot_id,
            version_refs=episode.version_refs,
            trajectory=episode.trajectory,
            outcome=episode.outcome,
            human_feedback=dict(episode.human_feedback),
            metrics=dict(episode.metrics),
            attribution=dict(episode.attribution),
            root_cause=episode.root_cause,
            confidence=episode.confidence,
            scope_level=episode.scope_level or DEFAULT_SCOPE_LEVEL,
            created_at=created_at,
        )
        conn.execute(
            """
            INSERT INTO episodes (
                id, session_id, turn_id, execution_subject_id, objective,
                input_summary, expected_outcome, run_snapshot_id, version_refs,
                trajectory, outcome, human_feedback, metrics, attribution,
                root_cause, confidence, scope_level, created_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?
            )
            ON CONFLICT(session_id, turn_id, execution_subject_id) DO UPDATE SET
                objective = excluded.objective,
                input_summary = excluded.input_summary,
                expected_outcome = excluded.expected_outcome,
                run_snapshot_id = excluded.run_snapshot_id,
                version_refs = excluded.version_refs,
                trajectory = excluded.trajectory,
                outcome = excluded.outcome,
                human_feedback = excluded.human_feedback,
                metrics = excluded.metrics,
                attribution = excluded.attribution,
                root_cause = excluded.root_cause,
                confidence = excluded.confidence,
                scope_level = excluded.scope_level
            """,
            _episode_to_row(persisted),
        )
        return persisted

    def get_episode(self, episode_id: str) -> Episode | None:
        """Return one episode by id, or ``None`` if absent."""
        conn = self._connect()
        row = conn.execute(
            f"SELECT {', '.join(_EPISODE_COLUMNS)} FROM episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_episode(row)

    def list_episodes(self, session_id: str) -> list[Episode]:
        """Return all episodes for a session, oldest first.

        Ordered by ``created_at`` with the implicit ``rowid`` as the tiebreaker:
        two turns finalized within the same millisecond (common on a warm cache)
        must still surface in insertion order, so ``rowid`` — not the random
        UUID ``id`` — breaks the tie.
        """
        conn = self._connect()
        rows = conn.execute(
            f"SELECT {', '.join(_EPISODE_COLUMNS)} FROM episodes "
            "WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (session_id,),
        ).fetchall()
        return [_row_to_episode(row) for row in rows]


# ---------------------------------------------------------------------------
# Row (de)serialization
# ---------------------------------------------------------------------------


def _episode_to_row(episode: Episode) -> tuple[Any, ...]:
    """Bind an episode to the column order in ``_EPISODE_COLUMNS``."""
    return (
        episode.id,
        episode.session_id,
        episode.turn_id,
        episode.execution_subject_id,
        episode.objective,
        episode.input_summary,
        episode.expected_outcome,
        episode.run_snapshot_id,
        json.dumps(list(episode.version_refs)) if episode.version_refs else None,
        episode.trajectory.to_json(),
        episode.outcome,
        json.dumps(dict(episode.human_feedback)) if episode.human_feedback else None,
        json.dumps(dict(episode.metrics)) if episode.metrics else None,
        json.dumps(dict(episode.attribution)) if episode.attribution else None,
        episode.root_cause,
        episode.confidence,
        episode.scope_level,
        episode.created_at,
    )


def _row_to_episode(row: tuple[Any, ...]) -> Episode:
    """Materialize an episode from a column-ordered row."""
    payload = dict(zip(_EPISODE_COLUMNS, row))
    version_refs_raw = payload.get("version_refs")
    return Episode(
        id=str(payload["id"]),
        session_id=str(payload["session_id"]),
        turn_id=str(payload["turn_id"]),
        execution_subject_id=str(payload["execution_subject_id"]),
        objective=payload.get("objective"),
        input_summary=payload.get("input_summary"),
        expected_outcome=payload.get("expected_outcome"),
        run_snapshot_id=payload.get("run_snapshot_id"),
        version_refs=tuple(json.loads(version_refs_raw)) if version_refs_raw else (),
        trajectory=EpisodeTrajectory.from_json(str(payload["trajectory"])),
        outcome=payload.get("outcome"),
        human_feedback=json.loads(payload["human_feedback"]) if payload.get("human_feedback") else {},
        metrics=json.loads(payload["metrics"]) if payload.get("metrics") else {},
        attribution=json.loads(payload["attribution"]) if payload.get("attribution") else {},
        root_cause=payload.get("root_cause"),
        confidence=payload.get("confidence"),
        scope_level=str(payload.get("scope_level") or DEFAULT_SCOPE_LEVEL),
        created_at=int(payload["created_at"]),
    )


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "DEFAULT_SCOPE_LEVEL",
    "Episode",
    "EpisodeStore",
    "EpisodeTrajectory",
    "episodes_database_path",
    "project_trajectory",
]
