# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Experience memory store and pure-function miner (Issue #10 / C2).

Sits one layer above :mod:`jiuwenswarm.observability.episode_store`: the miner
turns a batch of episodes into candidate ``Experience`` records (no LLM, no
store, no side effects — deterministic clustering with a quality gate), and the
store persists them in a shared ``experiences.db`` next to ``episodes.db``.

Experience memory is the read source for the retriever (C3, #11): scope-priority
recall with per-turn single injection. The 4-level scope
(user/project/tenant/global) and candidate/active lifecycle mirror the cangming
``evolution_experiences`` contract, minus the Go/MySQL dual-dialect baggage.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAlias

from jiuwenswarm.observability.episode_store import Episode

ExperienceScope: TypeAlias = Literal["user", "project", "tenant", "global"]
ExperienceStatus: TypeAlias = Literal["candidate", "active", "deprecated", "expired"]

DEFAULT_SCOPE_LEVEL: ExperienceScope = "user"
DEFAULT_STATUS: ExperienceStatus = "candidate"

# Quality gate: an experience is only ``active`` once it has enough independent
# evidence, enough confidence, and a non-trivial body. Below any threshold it
# stays a ``candidate`` awaiting more episodes. Bodies too short to be useful are
# dropped entirely.
_MIN_EVIDENCE_FOR_ACTIVE = 2
_MIN_CONFIDENCE_FOR_ACTIVE = 0.3
_MIN_CONTENT_CHARS = 20
_CONFIDENCE_PER_EVIDENCE = 0.2

_EXPERIENCE_COLUMNS = (
    "id",
    "title",
    "content",
    "search_tokens",
    "source_episode_ids",
    "evidence_count",
    "applicable_when",
    "not_applicable_when",
    "target_hints",
    "scope_level",
    "confidence",
    "use_count",
    "success_count",
    "failure_count",
    "status",
    "version",
    "created_at",
    "last_validated_at",
    "expires_at",
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS experiences (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    search_tokens TEXT,
    source_episode_ids TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    applicable_when TEXT,
    not_applicable_when TEXT,
    target_hints TEXT,
    scope_level TEXT NOT NULL DEFAULT 'user',
    confidence REAL,
    use_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'candidate',
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    last_validated_at INTEGER,
    expires_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_experiences_scope_status
    ON experiences(scope_level, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiences_source
    ON experiences(source_episode_ids);
"""


@dataclass(frozen=True, slots=True)
class Experience:
    """One mined experience record.

    Attributes:
        id: Stable identifier (UUID). Empty until persisted.
        title: Short title (defaults to ``content``).
        content: The strategy statement (e.g. ``avoid <target>``).
        search_tokens: Lexical tokens used by :meth:`ExperienceStore.search`.
        source_episode_ids: The episode ids this experience was mined from.
        evidence_count: Number of episodes in the source cluster.
        applicable_when / not_applicable_when: Optional guard clauses.
        target_hints: The targets this experience addresses.
        scope_level: Memory scope (user/project/tenant/global).
        confidence: Confidence in the outcome, in ``[0, 1]``.
        use_count / success_count / failure_count: Attribution counters.
        status: candidate / active / deprecated / expired.
        version: Monotonic version for safe re-injection.
        created_at: Unix-millisecond ingestion timestamp. Zero until persisted.
        last_validated_at: When the experience last passed validation.
        expires_at: Optional expiry timestamp.
    """

    content: str
    title: str = ""
    id: str = ""
    search_tokens: tuple[str, ...] = ()
    source_episode_ids: tuple[str, ...] = ()
    evidence_count: int = 0
    applicable_when: str | None = None
    not_applicable_when: str | None = None
    target_hints: tuple[str, ...] = ()
    scope_level: ExperienceScope = DEFAULT_SCOPE_LEVEL
    confidence: float | None = None
    use_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    status: ExperienceStatus = DEFAULT_STATUS
    version: int = 1
    created_at: int = 0
    last_validated_at: int | None = None
    expires_at: int | None = None


def mine(
    episodes: Sequence[Episode],
    scope: ExperienceScope = DEFAULT_SCOPE_LEVEL,
) -> list[Experience]:
    """Project a batch of episodes into candidate experiences (pure function).

    Clusters episodes by their target (objective, else the tools they used),
    then classifies each cluster by its outcome mix:

    - all failures → ``avoid <target>``
    - all successes → ``use <target>``
    - mixed failure+success → ``retry with <target>``
    - otherwise → ``observe <target>``

    The quality gate then drops bodies too short to be useful and marks the rest
    ``active`` only when evidence and confidence are both sufficient.

    Args:
        episodes: The episodes to mine (typically one session's finalized turns).
        scope: The memory scope to stamp on the produced experiences.

    Returns:
        Mined experiences. Deterministic: the same input yields the same output,
        in target-sorted order. No store, no LLM, no side effects.
    """
    clusters: dict[str, list[Episode]] = {}
    for episode in episodes:
        target = (episode.objective or "").strip()
        if not target:
            target = " ".join(episode.trajectory.tools).strip()
        if not target:
            target = "unknown"
        clusters.setdefault(target, []).append(episode)

    out: list[Experience] = []
    for target in sorted(clusters):
        members = clusters[target]
        outcomes = [(ep.outcome or "").strip().lower() for ep in members]
        failures = sum(1 for o in outcomes if o == "failure")
        successes = sum(1 for o in outcomes if o == "success")
        if successes and failures:
            verb = "retry with"
        elif failures and not successes:
            verb = "avoid"
        elif successes and not failures:
            verb = "use"
        else:
            verb = "observe"
        content = f"{verb} {target}"
        if len(content) <= _MIN_CONTENT_CHARS:
            continue
        evidence_count = len(members)
        confidence = min(1.0, _CONFIDENCE_PER_EVIDENCE * evidence_count)
        status: ExperienceStatus = (
            "active"
            if (
                evidence_count >= _MIN_EVIDENCE_FOR_ACTIVE
                and confidence >= _MIN_CONFIDENCE_FOR_ACTIVE
                and len(content) > _MIN_CONTENT_CHARS
            )
            else "candidate"
        )
        source_ids = tuple(sorted(ep.id for ep in members if ep.id))
        out.append(
            Experience(
                id="",
                title=content,
                content=content,
                search_tokens=tuple(sorted(set(target.split()))),
                source_episode_ids=source_ids,
                evidence_count=evidence_count,
                target_hints=(target,),
                scope_level=scope,
                confidence=confidence,
                use_count=0,
                success_count=successes,
                failure_count=failures,
                status=status,
                version=1,
                created_at=0,
                last_validated_at=None,
                expires_at=None,
            )
        )
    return out


def experiences_database_path(workspace: Path | None = None) -> Path:
    """Return the shared experiences database path under the trace root.

    Args:
        workspace: Optional data root override (for isolated tests). When
            ``None`` the real user workspace is resolved lazily.

    Returns:
        ``<workspace>/.trace/experiences.db`` (sibling of ``episodes.db``).
    """
    if workspace is None:
        from jiuwenswarm.common.utils import get_user_workspace_dir

        workspace = get_user_workspace_dir()
    return Path(workspace) / ".trace" / "experiences.db"


class ExperienceStore:
    """Synchronous SQLite store for experiences, sibling of :class:`EpisodeStore`.

    Writes are synchronous (stdlib :mod:`sqlite3`): experiences land once per
    mining pass, not on a hot stream. ``ensure`` is idempotent and additive and
    does not touch the trajectory or episode store schemas.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._db_path, timeout=5.0, isolation_level=None, check_same_thread=False
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        return conn

    def ensure(self) -> None:
        """Create the experiences table and indexes if they do not yet exist."""
        self._connect().executescript(_SCHEMA_SQL)

    def close(self) -> None:
        """Close the underlying connection. Safe to call once."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- CRUD ----------------------------------------------------------

    def create(self, experience: Experience) -> Experience:
        """Persist one experience, idempotent on its source episode ids.

        Re-mining the same episodes produces no duplicate: the canonical
        ``source_episode_ids`` JSON is the idempotency key.

        Args:
            experience: The experience to persist. ``id`` and ``created_at``
                are assigned by the store when unset.

        Returns:
            The persisted experience with ``id`` and timestamps filled in.
        """
        conn = self._connect()
        source_key = _canonical_source_ids(experience.source_episode_ids)
        existing = conn.execute(
            "SELECT id FROM experiences WHERE source_episode_ids = ?",
            (source_key,),
        ).fetchone()
        if existing:
            return self.get(existing[0]) or experience  # type: ignore[return-value]
        now = int(time.time() * 1000)
        row_id = experience.id or str(uuid.uuid4())
        created_at = experience.created_at or now
        last_validated = experience.last_validated_at
        if last_validated is None and experience.status == "active":
            last_validated = now
        persisted = Experience(
            id=row_id,
            content=experience.content,
            title=experience.title or experience.content,
            search_tokens=experience.search_tokens,
            source_episode_ids=experience.source_episode_ids,
            evidence_count=experience.evidence_count,
            applicable_when=experience.applicable_when,
            not_applicable_when=experience.not_applicable_when,
            target_hints=experience.target_hints,
            scope_level=experience.scope_level or DEFAULT_SCOPE_LEVEL,
            confidence=experience.confidence,
            use_count=experience.use_count,
            success_count=experience.success_count,
            failure_count=experience.failure_count,
            status=experience.status,
            version=experience.version,
            created_at=created_at,
            last_validated_at=last_validated,
            expires_at=experience.expires_at,
        )
        conn.execute(
            f"INSERT INTO experiences ({', '.join(_EXPERIENCE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _EXPERIENCE_COLUMNS)})",
            _experience_to_row(persisted),
        )
        return persisted

    def update_status(self, experience_id: str, status: ExperienceStatus) -> Experience:
        """Update an experience's status, returning the updated record.

        Args:
            experience_id: The experience to update.
            status: The new status.

        Returns:
            The updated experience.

        Raises:
            KeyError: If the experience is absent.
        """
        conn = self._connect()
        now = int(time.time() * 1000)
        last_validated = now if status == "active" else None
        conn.execute(
            "UPDATE experiences SET status = ?, last_validated_at = COALESCE(?, last_validated_at), "
            "version = version + 1 WHERE id = ?",
            (status, last_validated, experience_id),
        )
        updated = self.get(experience_id)
        if updated is None:
            raise KeyError(f"experience {experience_id!r} not found")
        return updated

    def get(self, experience_id: str) -> Experience | None:
        """Return one experience by id, or ``None`` if absent."""
        row = self._connect().execute(
            f"SELECT {', '.join(_EXPERIENCE_COLUMNS)} FROM experiences WHERE id = ?",
            (experience_id,),
        ).fetchone()
        return _row_to_experience(row) if row is not None else None

    def list_experiences(self) -> list[Experience]:
        """Return all experiences, newest first.

        Ordered by ``created_at`` with the implicit ``rowid`` as the tiebreaker,
        mirroring :meth:`EpisodeStore.list_episodes`: two experiences stamped in
        the same mining-pass millisecond surface in insertion order, not by the
        random UUID ``id``.
        """
        rows = self._connect().execute(
            f"SELECT {', '.join(_EXPERIENCE_COLUMNS)} FROM experiences "
            "ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return [_row_to_experience(row) for row in rows]

    def search(self, token: str) -> list[Experience]:
        """Return experiences whose content or tokens contain ``token``.

        A lexical, substring search (no vectors): the known gap cangming also
        carries. Vector recall is a separate, later concern.
        """
        like = f"%{token}%"
        rows = self._connect().execute(
            f"SELECT {', '.join(_EXPERIENCE_COLUMNS)} FROM experiences "
            "WHERE content LIKE ? OR search_tokens LIKE ? "
            "ORDER BY confidence DESC NULLS LAST, created_at DESC",
            (like, like),
        ).fetchall()
        return [_row_to_experience(row) for row in rows]


# ---------------------------------------------------------------------------
# Row (de)serialization
# ---------------------------------------------------------------------------


def _canonical_source_ids(source_episode_ids: tuple[str, ...]) -> str:
    return json.dumps(sorted(source_episode_ids))


def _experience_to_row(experience: Experience) -> tuple[Any, ...]:
    return (
        experience.id,
        experience.title,
        experience.content,
        json.dumps(list(experience.search_tokens)) if experience.search_tokens else None,
        _canonical_source_ids(experience.source_episode_ids),
        experience.evidence_count,
        experience.applicable_when,
        experience.not_applicable_when,
        json.dumps(list(experience.target_hints)) if experience.target_hints else None,
        experience.scope_level,
        experience.confidence,
        experience.use_count,
        experience.success_count,
        experience.failure_count,
        experience.status,
        experience.version,
        experience.created_at,
        experience.last_validated_at,
        experience.expires_at,
    )


def _row_to_experience(row: tuple[Any, ...]) -> Experience:
    payload = dict(zip(_EXPERIENCE_COLUMNS, row))
    tokens_raw = payload.get("search_tokens")
    hints_raw = payload.get("target_hints")
    source_raw = payload.get("source_episode_ids")
    return Experience(
        id=str(payload["id"]),
        title=str(payload["title"]),
        content=str(payload["content"]),
        search_tokens=tuple(json.loads(tokens_raw)) if tokens_raw else (),
        source_episode_ids=tuple(json.loads(source_raw)) if source_raw else (),
        evidence_count=int(payload.get("evidence_count") or 0),
        applicable_when=payload.get("applicable_when"),
        not_applicable_when=payload.get("not_applicable_when"),
        target_hints=tuple(json.loads(hints_raw)) if hints_raw else (),
        scope_level=str(payload.get("scope_level") or DEFAULT_SCOPE_LEVEL),  # type: ignore[arg-type]
        confidence=payload.get("confidence"),
        use_count=int(payload.get("use_count") or 0),
        success_count=int(payload.get("success_count") or 0),
        failure_count=int(payload.get("failure_count") or 0),
        status=str(payload.get("status") or DEFAULT_STATUS),  # type: ignore[arg-type]
        version=int(payload.get("version") or 1),
        created_at=int(payload.get("created_at") or 0),
        last_validated_at=payload.get("last_validated_at"),
        expires_at=payload.get("expires_at"),
    )


__all__ = [
    "DEFAULT_SCOPE_LEVEL",
    "DEFAULT_STATUS",
    "Experience",
    "ExperienceScope",
    "ExperienceStatus",
    "ExperienceStore",
    "experiences_database_path",
    "mine",
]
