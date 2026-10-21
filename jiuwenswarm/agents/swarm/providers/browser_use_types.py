# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Browser-use governance domain types (Issue #2 / A1, slice 2a).

Pure types for the governed browser-use surface layered onto the existing
``SWARM_BROWSER_AGENT`` sub-agent. Mirrors the cangming ``browser_use`` tool
contract: a closed 18-action space, a structured-result verdict model
(``not_executed`` / ``execution_uncertain`` / ``executed`` + approval/lock
verdicts), an approval-target identity predicate, and a snapshot ref table.

No Playwright, no CDP, no Chrome. The behavioural wiring (snapshot JS
injection, ref resolution against a live DOM, managed Chrome lifecycle,
management RPCs) lands in slices 2b/2c and consumes these types.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

# ---------------------------------------------------------------------------
# Closed 17-action space
# ---------------------------------------------------------------------------

BrowserUseAction: TypeAlias = Literal[
    "snapshot",
    "navigate",
    "click",
    "type",
    "scroll",
    "screenshot",
    "get_text",
    "get_html",
    "wait_for",
    "go_back",
    "go_forward",
    "new_tab",
    "close_tab",
    "switch_tab",
    "list_tabs",
    "get_url",
    "get_title",
    "evaluate",
]

BROWSER_USE_ACTIONS: frozenset[str] = frozenset(
    {
        "snapshot",
        "navigate",
        "click",
        "type",
        "scroll",
        "screenshot",
        "get_text",
        "get_html",
        "wait_for",
        "go_back",
        "go_forward",
        "new_tab",
        "close_tab",
        "switch_tab",
        "list_tabs",
        "get_url",
        "get_title",
        "evaluate",
    }
)

# ---------------------------------------------------------------------------
# Verdict model
# ---------------------------------------------------------------------------

BrowserVerdict: TypeAlias = Literal[
    "executed",
    "execution_uncertain",
    "not_executed",
    "denied",
    "approval_pending",
    "busy",
]

# Pre-dispatch failures (stale ref, element not found) and transient lock
# contention are replayable; anything that may have reached the page is not,
# because replaying an uncertain mutation could double-execute real input.
VERDICT_RETRYABLE: Mapping[BrowserVerdict, bool] = {
    "executed": False,
    "not_executed": True,
    "execution_uncertain": False,
    "denied": False,
    "approval_pending": False,
    "busy": True,
}


def is_retryable(verdict: BrowserVerdict) -> bool:
    """Return whether a browser verdict permits a safe replay.

    Args:
        verdict: One of the ``BrowserVerdict`` values.

    Returns:
        True for ``not_executed`` and ``busy``; False otherwise.

    Raises:
        ValueError: If ``verdict`` is not a known verdict.
    """
    if verdict not in VERDICT_RETRYABLE:
        raise ValueError(f"unknown browser-use verdict: {verdict!r}")
    return VERDICT_RETRYABLE[verdict]


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    """Structured result of one browser action, as the model should see it.

    Expected failures (element not found, stale ref) are *results*, not
    exceptions: ``verdict`` carries the outcome and ``retryable`` is derived
    from it so the model never has to guess whether to retry.

    Attributes:
        action: The action that was attempted.
        verdict: The outcome verdict.
        url_before: Page URL before the action, if observed.
        url_after: Page URL after the action, if observed.
        text_verification: The text the action was meant to affect, if any.
        hint: A short human-readable next-step hint for the model.
        target: A label for the target element/tab the action addressed.
    """

    action: str
    verdict: BrowserVerdict
    url_before: str | None = None
    url_after: str | None = None
    text_verification: str | None = None
    hint: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if self.verdict not in VERDICT_RETRYABLE:
            raise ValueError(f"unknown browser-use verdict: {self.verdict!r}")

    @property
    def retryable(self) -> bool:
        """Whether the model may safely retry this action."""
        return is_retryable(self.verdict)

    @property
    def url_changed(self) -> bool:
        """Whether the page URL differs between before and after observations."""
        if self.url_before is None or self.url_after is None:
            return False
        return self.url_before != self.url_after


# ---------------------------------------------------------------------------
# Approval-target identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrowserTargetIdentity:
    """Identity bound to a browser approval ticket, re-verified on resume.

    The security boundary is ``(instance_key, origin)``: the same Chrome
    instance and the same web origin. A tab switch within the same origin
    (different ``target_id``) is tolerated; a cross-site redirect (different
    ``origin``) or a different Chrome instance is not — the bound target has
    moved, and the action must be refused and re-observed.

    Attributes:
        instance_key: Stable key for the Chrome instance (e.g. the managed
            browser's debug port + profile key).
        target_id: The CDP ``TargetID`` of the tab the action targeted.
        origin: The serialized origin (scheme+host+port) of the page.
    """

    instance_key: str
    target_id: str | None = None
    origin: str | None = None

    def covers(self, other: "BrowserTargetIdentity") -> bool:
        """Return True if ``other`` is close enough to honour the bound ticket.

        Args:
            other: The current live target identity observed at resume.

        Returns:
            True iff ``other`` shares this identity's Chrome instance and web
            origin. Tab switches within the same origin are covered; cross-site
            redirects and instance changes are not.
        """
        if not self.instance_key or self.instance_key != other.instance_key:
            return False
        if not self.origin or self.origin != other.origin:
            return False
        return True


# ---------------------------------------------------------------------------
# Snapshot ref table
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotElement:
    """One retained DOM element from a snapshot, addressable by its ref.

    The ref is an opaque key the snapshot JS hands the model; resolving it back
    to a live node must re-validate that the node's label/tag/role have not
    changed, or the action fails as ``not_executed`` (stale ref, retryable).

    Attributes:
        ref: The opaque reference string (e.g. ``e1``).
        css_path: A unique CSS selector for the element.
        tag: The element's tag name.
        role: The element's ARIA role, if any.
        label: The element's accessible label/text, if any.
        bbox: The element's bounding box ``(x, y, w, h)`` in page pixels.
    """

    ref: str
    css_path: str
    tag: str
    role: str | None = None
    label: str | None = None
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)

    def is_stale(self, *, label: str | None, tag: str, role: str | None) -> bool:
        """Return True if the live node no longer matches this snapshot entry.

        Args:
            label: The live node's current accessible label.
            tag: The live node's current tag name.
            role: The live node's current ARIA role.

        Returns:
            True if any of tag/role/label differs from the snapshot, meaning
            the ref points at a node that has changed and must not be acted on.
        """
        if tag != self.tag:
            return True
        if role != self.role:
            return True
        if (label or None) != (self.label or None):
            return True
        return False


@dataclass(frozen=True, slots=True)
class SnapshotRefTable:
    """The ref→element table a ``snapshot`` action produces.

    Refs are invalidated on navigate/reconnect; a fresh snapshot is then
    required. The table is bounded so a single snapshot never floods the model.

    Attributes:
        elements: The retained snapshot elements, keyed by their ``ref``.
        max_elements: The cap on retained elements per snapshot.
    """

    elements: tuple[SnapshotElement, ...] = ()
    max_elements: int = 200

    def __post_init__(self) -> None:
        if self.max_elements < 1:
            raise ValueError("max_elements must be a positive integer")
        if len(self.elements) > self.max_elements:
            raise ValueError(
                f"snapshot retains {len(self.elements)} elements but max_elements"
                f" is {self.max_elements}"
            )

    def resolve(self, ref: str) -> SnapshotElement | None:
        """Return the retained element for ``ref``, or ``None`` if absent."""
        for element in self.elements:
            if element.ref == ref:
                return element
        return None


__all__ = [
    "BROWSER_USE_ACTIONS",
    "BrowserUseAction",
    "BrowserActionResult",
    "BrowserTargetIdentity",
    "BrowserVerdict",
    "VERDICT_RETRYABLE",
    "SnapshotElement",
    "SnapshotRefTable",
    "is_retryable",
]
