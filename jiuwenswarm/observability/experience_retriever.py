# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Experience retriever and injection attribution (Issue #11 / C3).

Reads the experiences mined by :mod:`jiuwenswarm.observability.experience_store`
and injects a bounded, scope-prioritized recall into the agent's system
message once per turn. Mirrors the cangming ``ExperienceRetriever`` /
``experience_bridge`` contract (#597):

- read-only against the experience store (the retriever never creates an
  experience);
- per-turn single injection (a second ``retrieve`` for the same
  ``(run_id, turn_id)`` returns the cached content with
  ``already_injected=True`` and writes no new attribution);
- fail-closed (any exception → empty result with ``source="unavailable"``,
  never fabricated, never a Python-side fallback that invents content);
- idempotent ``injection_records`` so re-injection of the same run/experience
  is recorded exactly once.

Pure Python + stdlib :mod:`sqlite3`; shares ``experiences.db`` with the store.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias

from jiuwenswarm.observability.experience_store import (
    DEFAULT_SCOPE_LEVEL,
    Experience,
    ExperienceScope,
    ExperienceStore,
)

# Scope priority for recall: a user-scoped experience outranks a project-scoped
# one, which outranks tenant, which outranks global. Higher wins conflicts.
SCOPE_PRIORITY: dict[str, int] = {"user": 4, "project": 3, "tenant": 2, "global": 1}

DEFAULT_TOKEN_BUDGET = 2000
DEFAULT_TOP_K = 5
_RECALL_HEADER = "# Accumulated Experience\nThe following strategies have been validated; apply them when relevant.\n"

_INJECTION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS injection_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    session_id TEXT,
    experience_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    score REAL,
    scope_level TEXT NOT NULL,
    injected_at INTEGER NOT NULL,
    UNIQUE(run_id, experience_id)
);
CREATE INDEX IF NOT EXISTS idx_injections_run ON injection_records(run_id);
"""


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """One retriever call.

    Attributes:
        objective: The turn's objective (primary search text).
        scope: The scope to recall within (experiences at this scope or wider
            are considered; wider = lower priority).
        run_id / session_id / turn_id: The turn the injection belongs to. A
            repeat retrieve for the same ``(run_id, turn_id)`` is a cache hit.
        agent_name / skill_id / tool_name: Additional search hints.
        token_budget: Soft cap on the injected content's word count.
        top_k: Maximum experiences to inject.
    """

    objective: str
    scope: ExperienceScope
    run_id: str
    session_id: str
    turn_id: str
    agent_name: str = ""
    skill_id: str = ""
    tool_name: str = ""
    token_budget: int = DEFAULT_TOKEN_BUDGET
    top_k: int = DEFAULT_TOP_K


@dataclass(frozen=True, slots=True)
class InjectionRecord:
    """Attribution row for one injected experience.

    Attributes:
        experience_id: The experience that was injected.
        run_id / session_id: The turn the injection served.
        version: The experience version at injection time.
        rank: 1-based injection rank.
        score: The confidence score used to rank.
        scope_level: The experience's scope.
        injected_at: Unix-millisecond injection timestamp.
    """

    experience_id: str
    run_id: str
    session_id: str
    version: int
    rank: int
    score: float | None
    scope_level: str
    injected_at: int


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The retriever's answer for one turn.

    Attributes:
        content: The formatted recall text to inject (empty on miss/failure).
        records: Attribution for each injected experience (empty on cache hit
            or failure).
        truncated: True if Top-K or token-budget dropped any candidate.
        version: Retrieval protocol version (0 on failure, 1 on success).
        source: ``experience_store`` on success, ``unavailable`` on failure.
        already_injected: True when this ``(run_id, turn_id)`` was already
            served this process — the caller must not re-inject.
    """

    content: str
    records: tuple[InjectionRecord, ...]
    truncated: bool
    version: int
    source: str
    already_injected: bool


def _verb(content: str) -> str:
    """Return the leading verb of an experience content (``avoid``/``use``/...)."""
    return content.split(" ", 1)[0] if content else ""


def _is_opposite(a: str, b: str) -> bool:
    """True when two experience verbs point in opposite directions.

    ``avoid`` is the only negative verb; any non-``avoid`` verb is positive.
    A conflict exists only when one side is ``avoid`` and the other is not.
    """
    return ("avoid" in (a, b)) and (a != b)


class ExperienceRetriever:
    """Read-only retriever with per-turn single injection and fail-closed I/O.

    The retriever owns its own connection to ``experiences.db`` for
    ``injection_records`` and ``use_count`` bookkeeping; it never creates or
    mutates an experience. The store it wraps handles search and reads.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._store = ExperienceStore(self._db_path)
        self._conn: sqlite3.Connection | None = None
        self._turn_cache: dict[tuple[str, str], RetrievalResult] = {}

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._db_path, timeout=5.0, isolation_level=None, check_same_thread=False
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        return conn

    def ensure(self) -> None:
        """Create the experiences + injection_records tables if absent."""
        self._store.ensure()
        self._connect().executescript(_INJECTION_SCHEMA_SQL)

    def close(self) -> None:
        self._store.close()
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- retrieval -----------------------------------------------------

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Retrieve and format the recall for one turn.

        Fail-closed: any exception returns an empty result with
        ``source="unavailable"``; the caller never sees a raise and never
        receives fabricated content.

        Args:
            query: The turn's retrieval request.

        Returns:
            The formatted recall, attribution, and cache/source flags.
        """
        try:
            cache_key = (query.run_id, query.turn_id)
            cached = self._turn_cache.get(cache_key)
            if cached is not None:
                # Per-turn single injection: serve the same content, flag it,
                # and write no new attribution.
                return RetrievalResult(
                    content=cached.content,
                    records=(),
                    truncated=cached.truncated,
                    version=cached.version,
                    source=cached.source,
                    already_injected=True,
                )
            return self._retrieve_fresh(query)
        except Exception:
            # Fail-closed: never raise, never fabricate. The caller falls back
            # to operating without recall.
            return RetrievalResult(
                content="",
                records=(),
                truncated=False,
                version=0,
                source="unavailable",
                already_injected=False,
            )

    def _retrieve_fresh(self, query: RetrievalQuery) -> RetrievalResult:
        tokens = [w for w in query.objective.split()]
        for extra in (query.skill_id, query.tool_name, query.agent_name):
            extra = (extra or "").strip()
            if extra:
                tokens.append(extra)
        # Deduplicate token order without losing relevance signal.
        seen_tokens: set[str] = set()
        candidates: list[Experience] = []
        deduped_ids: set[str] = set()
        for token in tokens:
            if not token or token in seen_tokens:
                continue
            seen_tokens.add(token)
            for exp in self._store.search(token):
                if exp.id in deduped_ids:
                    continue
                if exp.status != "active":
                    continue
                deduped_ids.add(exp.id)
                candidates.append(exp)

        # Rank by scope priority (desc), then confidence (desc), then recency.
        candidates.sort(
            key=lambda e: (
                -SCOPE_PRIORITY.get(e.scope_level, 0),
                -(e.confidence or 0.0),
                e.created_at,
            )
        )

        # Conflict resolution: within one target, opposite verbs (avoid vs
        # non-avoid) keep the higher-priority experience and drop the lower.
        resolved: list[Experience] = []
        target_verb: dict[str, str] = {}
        for exp in candidates:
            target = exp.target_hints[0] if exp.target_hints else exp.content
            verb = _verb(exp.content)
            existing = target_verb.get(target)
            if existing is not None and _is_opposite(existing, verb):
                # Opposite direction on the same target: keep the higher
                # priority (already placed), drop this lower one.
                continue
            resolved.append(exp)
            target_verb[target] = verb

        # Top-K.
        truncated = len(resolved) > query.top_k
        selected = resolved[: query.top_k]

        # Token-budget truncation (word count proxy).
        kept: list[Experience] = []
        total_words = len(_RECALL_HEADER.split())
        budget_truncated = False
        for exp in selected:
            words = len(exp.content.split())
            if kept and total_words + words > query.token_budget:
                budget_truncated = True
                break
            kept.append(exp)
            total_words += words
        truncated = truncated or budget_truncated

        if not kept:
            content = ""
            records: tuple[InjectionRecord, ...] = ()
        else:
            content = _RECALL_HEADER + "\n".join(exp.content for exp in kept)
            records = self._record_injections(query, kept)

        result = RetrievalResult(
            content=content,
            records=records,
            truncated=truncated,
            version=1,
            source="experience_store",
            already_injected=False,
        )
        self._turn_cache[(query.run_id, query.turn_id)] = result
        return result

    def _record_injections(
        self, query: RetrievalQuery, injected: list[Experience]
    ) -> tuple[InjectionRecord, ...]:
        conn = self._connect()
        now = int(time.time() * 1000)
        records: list[InjectionRecord] = []
        for rank, exp in enumerate(injected, start=1):
            # Idempotent on (run_id, experience_id): a repeat injection for the
            # same run is a no-op; use_count increments only on first insert.
            cur = conn.execute(
                "INSERT INTO injection_records "
                "(run_id, session_id, experience_id, version, rank, score, scope_level, injected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, experience_id) DO UPDATE SET "
                "version = excluded.version, rank = excluded.rank, score = excluded.score, "
                "scope_level = excluded.scope_level, injected_at = excluded.injected_at",
                (
                    query.run_id,
                    query.session_id,
                    exp.id,
                    exp.version,
                    rank,
                    exp.confidence,
                    exp.scope_level,
                    now,
                ),
            )
            records.append(
                InjectionRecord(
                    experience_id=exp.id,
                    run_id=query.run_id,
                    session_id=query.session_id,
                    version=exp.version,
                    rank=rank,
                    score=exp.confidence,
                    scope_level=exp.scope_level,
                    injected_at=now,
                )
            )
            # use_count++ on first insert only (changes() == 1).
            if cur.rowcount == 1 and cur.lastrowid:
                conn.execute(
                    "UPDATE experiences SET use_count = use_count + 1 WHERE id = ?",
                    (exp.id,),
                )
        return tuple(records)

    # -- attribution read ---------------------------------------------

    def list_injections(self, run_id: str) -> list[InjectionRecord]:
        """Return all injection attribution rows for one run, in rank order."""
        rows = self._connect().execute(
            "SELECT experience_id, run_id, session_id, version, rank, score, "
            "scope_level, injected_at FROM injection_records "
            "WHERE run_id = ? ORDER BY rank ASC",
            (run_id,),
        ).fetchall()
        return [
            InjectionRecord(
                experience_id=str(r[0]),
                run_id=str(r[1]),
                session_id="" if r[2] is None else str(r[2]),
                version=int(r[3]),
                rank=int(r[4]),
                score=r[5],
                scope_level=str(r[6]),
                injected_at=int(r[7]),
            )
            for r in rows
        ]


__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "DEFAULT_TOP_K",
    "ExperienceRetriever",
    "InjectionRecord",
    "RetrievalQuery",
    "RetrievalResult",
    "SCOPE_PRIORITY",
]
