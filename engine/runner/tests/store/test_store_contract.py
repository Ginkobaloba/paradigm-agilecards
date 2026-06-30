"""Repository contract tests.

Every test here runs against both stores via the parametrized `repo`
fixture (defined in conftest.py). The point is that the two
implementations are behaviourally interchangeable: that is what makes
the concrete store a swappable detail rather than a load-bearing
assumption.
"""
from __future__ import annotations

import pytest

from cards_runner.store.models import ActorType, Batch, CardEvent, EventType
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.repository import (
    CardNotFound,
    CardRepository,
    DuplicateCard,
)
from .store_support import make_card_text


def _new_card(repo: CardRepository, card_id: str, **kw: object) -> None:
    repo.create_card(card_text_to_record(make_card_text(card_id, **kw)))  # type: ignore[arg-type]


def test_create_and_get_roundtrips(repo: CardRepository) -> None:
    _new_card(repo, "b001-01-create")
    got = repo.get_card("b001-01-create")
    assert got is not None
    assert got.card_id == "b001-01-create"
    assert got.status == "backlog"
    assert got.points == 2


def test_get_missing_returns_none(repo: CardRepository) -> None:
    assert repo.get_card("does-not-exist") is None


def test_create_duplicate_raises(repo: CardRepository) -> None:
    _new_card(repo, "b001-02-dup")
    with pytest.raises(DuplicateCard):
        _new_card(repo, "b001-02-dup")


def test_create_writes_a_drafted_event(repo: CardRepository) -> None:
    _new_card(repo, "b001-03-drafted")
    events = repo.list_events("b001-03-drafted")
    assert len(events) == 1
    assert events[0].type == EventType.DRAFTED.value
    assert events[0].seq == 1


def test_query_cards_filters_by_status(repo: CardRepository) -> None:
    _new_card(repo, "b001-04-a")
    _new_card(repo, "b001-05-b")
    repo.transition("b001-05-b", to_status="done")
    assert {c.card_id for c in repo.query_cards(status="backlog")} == {"b001-04-a"}
    assert {c.card_id for c in repo.query_cards(status="done")} == {"b001-05-b"}


def test_count_cards(repo: CardRepository) -> None:
    assert repo.count_cards() == 0
    _new_card(repo, "b001-06-a")
    _new_card(repo, "b001-07-b")
    assert repo.count_cards() == 2


def test_update_card_fields(repo: CardRepository) -> None:
    _new_card(repo, "b001-08-upd")
    updated = repo.update_card_fields(
        "b001-08-upd", {"actual_tokens": 4242, "model_used": "claude-sonnet-4-6"}
    )
    assert updated.actual_tokens == 4242
    assert updated.model_used == "claude-sonnet-4-6"
    # A non-promoted field lands in the tail, not a column.
    repo.update_card_fields("b001-08-upd", {"sizing_note": "revised"})
    card = repo.get_card("b001-08-upd")
    assert card is not None
    assert card.frontmatter_extra["sizing_note"] == "revised"


def test_update_missing_card_raises(repo: CardRepository) -> None:
    with pytest.raises(CardNotFound):
        repo.update_card_fields("nope", {"actual_tokens": 1})


def test_transition_sets_status_and_appends_event(repo: CardRepository) -> None:
    _new_card(repo, "b001-09-trans")
    repo.transition(
        "b001-09-trans",
        to_status="blocked",
        fields={"merge_status": "conflict"},
        event_type=EventType.BLOCKED.value,
    )
    card = repo.get_card("b001-09-trans")
    assert card is not None
    assert card.status == "blocked"
    assert card.merge_status == "conflict"
    events = repo.list_events("b001-09-trans")
    assert [e.type for e in events] == [
        EventType.DRAFTED.value,
        EventType.BLOCKED.value,
    ]
    assert events[-1].payload["from"] == "backlog"
    assert events[-1].payload["to"] == "blocked"


def test_claim_succeeds_then_card_is_not_claimable(repo: CardRepository) -> None:
    _new_card(repo, "b001-10-claim")
    claimed = repo.claim_card("b001-10-claim", claimed_by="runner-1")
    assert claimed is not None
    assert claimed.status == "active"
    assert claimed.claimed_by == "runner-1"
    assert claimed.started_at is not None
    assert claimed.last_heartbeat is not None
    assert claimed.attempt_trace_id is not None
    # A second claim on a now-active card returns None.
    assert repo.claim_card("b001-10-claim", claimed_by="runner-2") is None


def test_claim_missing_card_returns_none(repo: CardRepository) -> None:
    assert repo.claim_card("ghost", claimed_by="runner-1") is None


def test_claim_appends_a_claimed_event(repo: CardRepository) -> None:
    _new_card(repo, "b001-11-claimev")
    repo.claim_card("b001-11-claimev", claimed_by="runner-9")
    events = repo.list_events("b001-11-claimev")
    assert [e.type for e in events] == [
        EventType.DRAFTED.value,
        EventType.CLAIMED.value,
    ]
    assert events[-1].actor_id == "runner-9"
    assert events[-1].actor_type == ActorType.RUNNER.value


def test_append_event_assigns_monotonic_seq(repo: CardRepository) -> None:
    _new_card(repo, "b001-12-seq")
    for _ in range(3):
        repo.append_event(
            CardEvent(card_id="b001-12-seq", type=EventType.HEARTBEAT.value)
        )
    # The drafted event is seq 1; the three heartbeats follow.
    assert [e.seq for e in repo.list_events("b001-12-seq")] == [1, 2, 3, 4]


def test_events_are_scoped_per_card(repo: CardRepository) -> None:
    _new_card(repo, "b001-13-x")
    _new_card(repo, "b001-14-y")
    repo.append_event(CardEvent(card_id="b001-13-x", type=EventType.HEARTBEAT.value))
    assert len(repo.list_events("b001-13-x")) == 2
    assert len(repo.list_events("b001-14-y")) == 1


def test_batches_and_monotonic_counter(repo: CardRepository) -> None:
    assert repo.next_batch_id() == "b001"
    assert repo.next_batch_id() == "b002"
    assert repo.next_batch_id() == "b003"
    repo.create_batch(Batch(batch_id="b003", manifest={"source": {"text": "hi"}}))
    got = repo.get_batch("b003")
    assert got is not None
    assert got.manifest["source"]["text"] == "hi"


def test_dependencies_from_card_and_explicit(repo: CardRepository) -> None:
    _new_card(repo, "b001-15-dep", depends_on=["b001-01-create", "b001-02-dup"])
    deps = repo.get_dependencies("b001-15-dep")
    assert set(deps) == {"b001-01-create", "b001-02-dup"}
    # add_dependency is idempotent.
    repo.add_dependency("b001-15-dep", "b001-01-create")
    assert sorted(repo.get_dependencies("b001-15-dep")) == sorted(deps)
    repo.add_dependency("b001-15-dep", "b001-99-new")
    assert "b001-99-new" in repo.get_dependencies("b001-15-dep")
    assert "b001-15-dep" in repo.get_dependents("b001-01-create")


def test_card_events_carry_actor_identity(repo: CardRepository) -> None:
    # Per-actor audit is the capability the filesystem substrate
    # could not provide; every event row carries actor fields.
    _new_card(repo, "b001-16-audit")
    repo.claim_card("b001-16-audit", claimed_by="runner-audit")
    repo.transition(
        "b001-16-audit",
        to_status="done",
        actor_id="verifier-7",
        actor_type=ActorType.VERIFIER.value,
        event_type=EventType.VERIFIED.value,
    )
    verified = [
        e for e in repo.list_events("b001-16-audit")
        if e.type == EventType.VERIFIED.value
    ][0]
    assert verified.actor_id == "verifier-7"
    assert verified.actor_type == ActorType.VERIFIER.value
