"""Tests for `cards_runner.daemon.reviewer_cost`."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from cards_runner.daemon.reviewer_cost import (
    ReviewerUsage,
    attribute_to_card,
    card_spent_usd_estimate,
    estimate_call_cost_usd,
    extract_usage_tokens,
    would_exceed_card_cap,
    would_exceed_reviewer_cap,
)
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository
from cards_runner.worker_stub.cost import Pricing

from tests.test_merge_gate import _card_text


# ---- extract_usage_tokens -------------------------------------------


@dataclass
class _UsageObj:
    input_tokens: int
    output_tokens: int


class _ResponseWithUsage:
    def __init__(self, in_t: int, out_t: int) -> None:
        self.usage = _UsageObj(input_tokens=in_t, output_tokens=out_t)


class _ResponseNoUsage:
    pass


def test_extract_usage_tokens_from_object_response() -> None:
    assert extract_usage_tokens(_ResponseWithUsage(100, 200)) == (100, 200)


def test_extract_usage_tokens_from_dict_response() -> None:
    assert extract_usage_tokens(
        {"usage": {"input_tokens": 50, "output_tokens": 75}}
    ) == (50, 75)


def test_extract_usage_tokens_handles_missing_usage() -> None:
    assert extract_usage_tokens(_ResponseNoUsage()) == (0, 0)
    assert extract_usage_tokens({}) == (0, 0)
    assert extract_usage_tokens(None) == (0, 0)


def test_extract_usage_tokens_handles_negative_or_null_values() -> None:
    assert extract_usage_tokens({"usage": {"input_tokens": None, "output_tokens": 5}}) == (0, 5)
    assert extract_usage_tokens({"usage": {"input_tokens": -10, "output_tokens": -20}}) == (0, 0)


# ---- ReviewerUsage.from_response ------------------------------------


def test_reviewer_usage_computes_cost_via_pricing() -> None:
    pricing = Pricing(table={"haiku": (1.0, 4.0)})
    response = _ResponseWithUsage(in_t=1_000_000, out_t=500_000)
    usage = ReviewerUsage.from_response(
        response, model_id="claude-haiku-4-5-20251001", pricing=pricing,
    )
    assert usage.input_tokens == 1_000_000
    assert usage.output_tokens == 500_000
    assert usage.total_tokens == 1_500_000
    # 1M input @ $1/M + 500k output @ $4/M = $1 + $2 = $3
    assert usage.cost_usd == pytest.approx(3.0)


def test_reviewer_usage_zero_when_no_usage_block() -> None:
    usage = ReviewerUsage.from_response(
        _ResponseNoUsage(), model_id="claude-sonnet-4-6",
    )
    assert usage.total_tokens == 0
    assert usage.cost_usd == 0.0


# ---- attribute_to_card ----------------------------------------------


def test_attribute_to_card_increments_actual_tokens(repo: SqliteRepository) -> None:
    record = card_text_to_record(_card_text("bAT-01", points=2))
    record.actual_tokens = 1000
    repo.create_card(record)
    record = repo.get_card("bAT-01")
    usage = ReviewerUsage(
        input_tokens=300, output_tokens=200, cost_usd=0.01, model_id="m",
    )
    new_total = attribute_to_card(repo, record, usage, tenant_id=record.tenant_id)
    assert new_total == 1500
    refreshed = repo.get_card("bAT-01")
    assert refreshed.actual_tokens == 1500


def test_attribute_to_card_skips_when_usage_zero(repo: SqliteRepository) -> None:
    record = card_text_to_record(_card_text("bAT-02", points=2))
    record.actual_tokens = 1000
    repo.create_card(record)
    record = repo.get_card("bAT-02")
    usage = ReviewerUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model_id="m")
    new_total = attribute_to_card(repo, record, usage, tenant_id=record.tenant_id)
    assert new_total == 1000
    refreshed = repo.get_card("bAT-02")
    assert refreshed.actual_tokens == 1000  # unchanged


def test_attribute_to_card_handles_null_prior(repo: SqliteRepository) -> None:
    record = card_text_to_record(_card_text("bAT-03", points=2))
    record.actual_tokens = None
    repo.create_card(record)
    record = repo.get_card("bAT-03")
    usage = ReviewerUsage(input_tokens=10, output_tokens=20, cost_usd=0.01, model_id="m")
    new_total = attribute_to_card(repo, record, usage, tenant_id=record.tenant_id)
    assert new_total == 30


# ---- estimate_call_cost_usd ------------------------------------------


def test_estimate_call_cost_usd_uses_pricing() -> None:
    pricing = Pricing(table={"sonnet": (3.0, 15.0)})
    cost = estimate_call_cost_usd(
        "claude-sonnet-4-6",
        est_input_tokens=1_000_000,
        max_output_tokens=1_000_000,
        pricing=pricing,
    )
    # 1M @ $3/M + 1M @ $15/M = $18
    assert cost == pytest.approx(18.0)


def test_estimate_call_cost_usd_clamps_negative() -> None:
    cost = estimate_call_cost_usd(
        "m", est_input_tokens=-100, max_output_tokens=-100,
    )
    assert cost == 0.0


# ---- card_spent_usd_estimate ----------------------------------------


def test_card_spent_usd_estimate_zero_when_no_tokens(repo: SqliteRepository) -> None:
    record = card_text_to_record(_card_text("bCSE-01", points=2))
    record.actual_tokens = None
    repo.create_card(record)
    record = repo.get_card("bCSE-01")
    assert card_spent_usd_estimate(record, model_id_hint="m") == 0.0


def test_card_spent_usd_estimate_proportional(repo: SqliteRepository) -> None:
    pricing = Pricing(table={"haiku": (1.0, 4.0)})
    record = card_text_to_record(_card_text("bCSE-02", points=2))
    record.actual_tokens = 1_000_000
    repo.create_card(record)
    record = repo.get_card("bCSE-02")
    # 1M tokens split 75/25 = 750k input + 250k output
    # = $0.75 + $1.00 = $1.75
    cost = card_spent_usd_estimate(
        record,
        model_id_hint="claude-haiku-4-5-20251001",
        pricing=pricing,
    )
    assert cost == pytest.approx(1.75)


# ---- would_exceed_*_cap ---------------------------------------------


def test_would_exceed_reviewer_cap_no_cap() -> None:
    assert would_exceed_reviewer_cap(None, already_spent_usd=0.0, projected_call_usd=999.0) is False


def test_would_exceed_reviewer_cap_under_cap() -> None:
    assert would_exceed_reviewer_cap(0.50, already_spent_usd=0.10, projected_call_usd=0.20) is False


def test_would_exceed_reviewer_cap_over_cap() -> None:
    assert would_exceed_reviewer_cap(0.50, already_spent_usd=0.40, projected_call_usd=0.20) is True


def test_would_exceed_card_cap_no_cap_on_card(repo: SqliteRepository) -> None:
    record = card_text_to_record(_card_text("bCC-01", points=2))
    record.actual_tokens = 0
    repo.create_card(record)
    record = repo.get_card("bCC-01")
    exceeds, cap, total = would_exceed_card_cap(
        record, projected_call_usd=999.0, model_id_hint="m",
    )
    assert exceeds is False
    assert cap is None


def test_would_exceed_card_cap_with_cap(repo: SqliteRepository) -> None:
    record = card_text_to_record(_card_text("bCC-02", points=2))
    record.actual_tokens = 0
    record.frontmatter_extra["cost_cap_usd"] = 0.01
    repo.create_card(record)
    record = repo.get_card("bCC-02")
    exceeds, cap, total = would_exceed_card_cap(
        record, projected_call_usd=1.0, model_id_hint="m",
    )
    assert exceeds is True
    assert cap == 0.01
    assert total == pytest.approx(1.0)


def test_would_exceed_card_cap_under_with_prior_spend(repo: SqliteRepository) -> None:
    record = card_text_to_record(_card_text("bCC-03", points=2))
    record.frontmatter_extra["cost_cap_usd"] = 100.0
    record.actual_tokens = 100  # tiny
    repo.create_card(record)
    record = repo.get_card("bCC-03")
    exceeds, cap, total = would_exceed_card_cap(
        record, projected_call_usd=1.0, model_id_hint="claude-haiku-4-5-20251001",
    )
    assert exceeds is False
    assert cap == 100.0
