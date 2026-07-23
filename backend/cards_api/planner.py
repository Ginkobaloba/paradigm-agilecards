"""Story planning seam (legacy ``lib/invoker.ts`` behind ``routes/stories.ts``).

The legacy backend shelled out to the ``claude`` CLI on the host to decompose
a story into planned cards, streaming progress as it went. The containerized
deployment has no such binary, so the CLI invoker's port is a documented
follow-up (P2, runner unification -- that chunk owns process supervision).
This module defines the interface the stories router plans against, plus the
deterministic offline implementation (port of the legacy STORIES_DEMO_INVOKER)
used for demos and tests.

Planner selection is by name via the ``PARADIGM_STORIES_PLANNER`` env var,
read at request time in the stories router so tests can monkeypatch it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class PlannerError(Exception):
    """Planning failed. ``stage`` names the pipeline step for the error frame."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass
class PlannedCard:
    card_id: str
    file: str
    title: str
    frontmatter: dict
    body: str


@dataclass
class PlanResult:
    cards: list[PlannedCard]
    manifest: dict | None


class StoryPlanner(Protocol):
    def plan(
        self,
        *,
        story: str,
        project_path: str | None,
        mode: str,
        deep_planning: bool,
        progress: Callable[[dict], None],
    ) -> PlanResult: ...


class DemoPlanner:
    """Deterministic offline planner: fixed ids, tiers, and one dependency
    edge, derived only from the story text -- no randomness, no clock reads --
    so tests can assert the exact dry-run payload."""

    def plan(
        self,
        *,
        story: str,
        project_path: str | None,
        mode: str,
        deep_planning: bool,
        progress: Callable[[dict], None],
    ) -> PlanResult:
        excerpt = story[:200]
        progress({"step": "plan", "agent": "planner", "message": "analyzing story"})
        progress({"step": "plan", "agent": "planner", "message": "decomposing into cards"})
        progress({"step": "plan", "agent": "planner", "message": "estimating tiers"})

        cards: list[PlannedCard] = []
        for n in (1, 2, 3):
            card_id = f"demo-{n:02d}"
            # One dependency edge: demo-02 depends on demo-01.
            depends_on = ["demo-01"] if n == 2 else []
            cards.append(
                PlannedCard(
                    card_id=card_id,
                    file=f"{card_id}.md",
                    title=f"Demo card {n}",
                    frontmatter={
                        "title": f"Demo card {n}",
                        "points": n,
                        "model": "sonnet-4-6",
                        "estimated_tokens": 20000,
                        "depends_on": depends_on,
                    },
                    body=f"## Story\n\n{excerpt}\n\n## Task\n\nDemo planned card {n} of 3.\n",
                )
            )
        manifest = {
            "planner": "demo",
            "mode": mode,
            "deep_planning": deep_planning,
            "card_count": len(cards),
        }
        return PlanResult(cards=cards, manifest=manifest)


def load_planner(name: str | None) -> StoryPlanner | None:
    """Resolve a planner by name. Unknown/empty names resolve to None (the
    stories router then answers 501): fail closed, never guess a planner."""
    if name == "demo":
        return DemoPlanner()
    return None
