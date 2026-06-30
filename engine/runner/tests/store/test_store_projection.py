"""Projection round-trip tests.

The load-bearing property: a card parsed and projected with
`verbatim=True` is byte-identical to its source. That is the
guarantee the v1 migration's losslessness rests on, so it is tested
directly and at the byte level, including the awkward cases (blank
lines around the fences, a missing `id`, a missing trailing newline,
a body that itself contains `---`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cards_runner.store.models import CardStatus
from cards_runner.store.projection import (
    ProjectionError,
    card_text_to_record,
    record_to_frontmatter,
    render_card_text,
)
from .store_support import make_card_text


def test_verbatim_roundtrip_is_byte_exact() -> None:
    text = make_card_text("b001-01-synthetic")
    record = card_text_to_record(text)
    assert render_card_text(record, verbatim=True) == text


def test_verbatim_roundtrip_preserves_blank_line_after_fence() -> None:
    # The closing fence is followed by a blank line, then the body. A
    # greedy fence regex eats that blank line; this asserts it does not.
    text = "---\nid: b1-01\nstatus: backlog\n---\n\n## Body\n\ntext here\n"
    record = card_text_to_record(text)
    assert record.body_md.startswith("\n## Body")
    assert render_card_text(record, verbatim=True) == text


def test_verbatim_roundtrip_no_trailing_newline() -> None:
    text = "---\nid: b1-02\nstatus: backlog\n---\n## Body no final newline"
    record = card_text_to_record(text)
    assert render_card_text(record, verbatim=True) == text


def test_verbatim_roundtrip_body_with_internal_fence_like_text() -> None:
    # A body that itself contains `---` must not confuse the parser.
    body = "\n## Notes\n\nSome prose.\n\n---\n\nMore prose after a rule.\n"
    text = make_card_text("b001-03-fences", body=body)
    record = card_text_to_record(text)
    assert record.body_md == body
    assert render_card_text(record, verbatim=True) == text


def test_promoted_fields_and_extra_split() -> None:
    text = make_card_text("b001-04-split", points=5, depends_on=["b001-01-x"])
    record = card_text_to_record(text)
    # `points` is a promoted typed column, coerced to int.
    assert record.points == 5
    assert isinstance(record.points, int)
    # `depends_on` is not promoted; it lives in the tail.
    assert record.frontmatter_extra["depends_on"] == ["b001-01-x"]
    assert "points" not in record.frontmatter_extra


def test_status_override_makes_subfolder_canonical() -> None:
    text = make_card_text("b001-05-override", status="backlog")
    record = card_text_to_record(text, status_override="active")
    assert record.status == CardStatus.ACTIVE.value


def test_amendment_status_field_value_normalizes() -> None:
    # The `amendments` subfolder pairs with the `awaiting_amendment_review`
    # field value. Parsing normalizes it; the non-verbatim projection
    # writes the long form back.
    text = make_card_text("b001-06-amend", status="awaiting_amendment_review")
    record = card_text_to_record(text)
    assert record.status == CardStatus.AMENDMENTS.value
    assert record_to_frontmatter(record)["status"] == "awaiting_amendment_review"


def test_card_id_falls_back_when_frontmatter_has_no_id() -> None:
    text = "---\nstatus: backlog\n---\n\n## Body\n"
    record = card_text_to_record(text, card_id_fallback="b001-07-fallback")
    assert record.card_id == "b001-07-fallback"


def test_missing_id_and_no_fallback_raises() -> None:
    with pytest.raises(ProjectionError):
        card_text_to_record("---\nstatus: backlog\n---\n\nbody\n")


def test_text_without_frontmatter_raises() -> None:
    with pytest.raises(ProjectionError):
        card_text_to_record("just a plain markdown file\n")


def test_timestamps_stay_strings_not_datetimes() -> None:
    # YAML would resolve bare ISO scalars to date/datetime objects,
    # which are not JSON-serializable. The loader keeps them as text.
    cascade = [{"from_tier": 2, "to_tier": 3, "at": "2026-05-02T10:00:00Z"}]
    text = make_card_text("b001-09-ts", cascade_history=cascade)
    record = card_text_to_record(text)
    assert record.created == "2026-05-01"
    assert isinstance(record.created, str)
    entry = record.frontmatter_extra["cascade_history"][0]
    assert entry["at"] == "2026-05-02T10:00:00Z"
    assert isinstance(entry["at"], str)


def test_nonverbatim_projection_reparses_to_same_fields() -> None:
    text = make_card_text("b001-08-nv", points=4, depends_on=["b001-02-y"])
    record = card_text_to_record(text)
    reparsed = card_text_to_record(render_card_text(record, verbatim=False))
    assert reparsed.card_id == record.card_id
    assert reparsed.points == record.points
    assert reparsed.status == record.status
    assert reparsed.frontmatter_extra["depends_on"] == ["b001-02-y"]
    assert reparsed.body_md == record.body_md


def test_real_example_card_roundtrips_if_present() -> None:
    # The committed example card is the realest fixture available.
    here = Path(__file__).resolve()
    name = "b001-03-add-rate-limit-middleware.md"
    candidates = [
        here.parents[2] / "fixtures" / name,
        here.parents[3] / "examples" / name,
    ]
    example = next((p for p in candidates if p.is_file()), None)
    if example is None:
        pytest.skip("example card not found in any known location")
    original = example.read_text(encoding="utf-8")
    record = card_text_to_record(original, card_id_fallback=example.stem)
    assert render_card_text(record, verbatim=True) == original
