"""In-memory, org-scoped card store.

This is the data layer the auth work guards (AC-CARDS-007). It is intentionally
small -- the full card CRUD (filesystem-backed, ranks, SSE) is a later chunk's
rewrite of the legacy Express backend. Every read/write here is keyed by
``org_id`` so isolation is enforced at the store boundary, not just the route.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass


@dataclass
class Card:
    id: str
    org_id: str
    title: str
    status: str = "backlog"

    def public_dict(self) -> dict:
        """Serializable view. ``org_id`` is included so isolation is auditable."""
        return asdict(self)


class CardStore:
    """A trivial dict-backed store. Not thread-safe; fine for v1 single-process."""

    def __init__(self) -> None:
        self._cards: dict[str, Card] = {}

    def add(self, card: Card) -> None:
        self._cards[card.id] = card

    def list_for_org(self, org_id: str) -> list[Card]:
        return [c for c in self._cards.values() if c.org_id == org_id]

    def get_for_org(self, card_id: str, org_id: str) -> Card | None:
        """Return the card only if it belongs to ``org_id`` -- otherwise None.

        Returning None (not the foreign card) is what lets the route answer 404
        rather than 403, so one org cannot probe another org's card ids.
        """
        card = self._cards.get(card_id)
        if card is None or card.org_id != org_id:
            return None
        return card

    def create(self, *, org_id: str, title: str) -> Card:
        card = Card(id=uuid.uuid4().hex, org_id=org_id, title=title)
        self._cards[card.id] = card
        return card


def default_store() -> CardStore:
    """Empty store used by the real app at boot (no seed data in production)."""
    return CardStore()
