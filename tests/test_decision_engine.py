"""Focused offline tests for the one-title decision receipt engine."""

import json

import pytest

from decision_engine import (
    DecisionRequest,
    HistoricalComparator,
    Offer,
    PurchaseConstraints,
    evaluate_decision,
)


def offer(**changes):
    values = {
        "title": "Example Movie",
        "title_id": "movie-123",
        "store": "itunes",
        "country": "us",
        "format_tier": "4K",
        "current_price": 4.99,
        "regular_price": 14.99,
    }
    values.update(changes)
    return Offer(**values)


def comparator(price=4.99, observed_on="2026-01-01", kind="historical_floor", trustworthy=True):
    return HistoricalComparator(price, observed_on, kind, trustworthy)


def decide(**changes):
    values = {
        "offer": offer(),
        "history": (comparator(),),
        "authoritative_atl": True,
    }
    values.update(changes)
    return evaluate_decision(DecisionRequest(**values))


def test_buy_with_neutral_defaults_and_serializable_discriminator():
    result = decide()
    payload = result.to_dict()
    assert payload["state"] == "decision"
    assert payload["verdict"] == "Buy"
    assert payload["objective_deal_strength"]["label"] == "strong"
    assert payload["personal_fit"]["assessment"] == "neutral_defaults"
    assert all(item["source"] == "default" for item in payload["applied_constraints"].values())
    assert payload["applied_constraints"]["budget_ceiling"]["default_meaning"] == "no budget ceiling applied"
    json.dumps(payload)


def test_wait_for_fair_price_and_sparse_recurrence_fallback_has_no_date_estimate():
    result = decide(
        offer=offer(current_price=5.99, regular_price=7.99),
        history=(comparator(price=4.99),),
        authoritative_atl=False,
    )
    assert result.verdict == "Wait"
    assert result.recurrence["eligible"] is False
    assert "broad_window" not in result.recurrence
    assert "assuming a date" in result.recurrence["guidance"]


def test_weak_offer_is_offer_specific_skip():
    result = decide(
        offer=offer(current_price=12.99, regular_price=14.99),
        history=(comparator(price=4.99),),
        authoritative_atl=False,
    )
    assert result.verdict == "Skip"
    assert "current selected-tier offer" in result.decisive_reason
    assert any("not a judgment of the title" in caveat for caveat in result.caveats)


def test_budget_hard_constraint_forces_offer_specific_skip():
    result = decide(constraints=PurchaseConstraints(budget_ceiling=3.99))
    assert result.verdict == "Skip"
    assert "above the request budget" in result.decisive_reason
    assert result.personal_fit["hard_constraint_conflicts"]


@pytest.mark.parametrize("required_format", ["SD", "HD", "4K"])
def test_required_format_is_minimum_capability(required_format):
    result = decide(constraints=PurchaseConstraints(required_format=required_format))

    assert result.verdict == "Buy"
    assert not result.personal_fit["hard_constraint_conflicts"]


def test_offer_below_required_minimum_format_is_offer_specific_skip():
    result = decide(
        offer=offer(format_tier="HD"),
        constraints=PurchaseConstraints(required_format="4K"),
    )

    assert result.verdict == "Skip"
    assert "below the required minimum 4K format" in result.decisive_reason
    assert result.personal_fit["hard_constraint_conflicts"]


def test_prior_comparable_is_not_a_floor_when_authoritative_atl_is_false():
    result = decide(
        offer=offer(current_price=13.99, regular_price=14.99),
        history=(comparator(price=14.99, observed_on=None, kind="prior_comparable"),),
        authoritative_atl=False,
    )

    position = result.objective_deal_strength["components"]["price_position"]
    assert result.verdict == "Skip"
    assert position["assessment"] == "above_unknown_historical_floor"
    assert position["points"] == 0
    assert position["observed_floor"] is None
    assert "lower historical price exists" in position["reason"]
    assert "historical floor is unknown" in position["reason"]


def test_upgrade_intent_uses_stricter_offer_value_bar():
    result = decide(
        offer=offer(current_price=5.99, regular_price=7.99),
        history=(comparator(price=4.99),),
        authoritative_atl=False,
        constraints=PurchaseConstraints(intent="upgrade"),
    )
    assert result.verdict == "Skip"
    assert "replacing an owned copy" in result.decisive_reason


def test_low_patience_can_buy_fair_offer_but_does_not_change_objective_strength():
    request = {
        "offer": offer(current_price=5.99, regular_price=7.99),
        "history": (comparator(price=4.99),),
        "authoritative_atl": False,
    }
    neutral = decide(**request)
    impatient = decide(**request, constraints=PurchaseConstraints(patience="low"))
    assert neutral.verdict == "Wait"
    assert impatient.verdict == "Buy"
    assert impatient.objective_deal_strength == neutral.objective_deal_strength


def test_flexible_patience_waits_when_strong_offer_is_not_confirmed_at_atl():
    request = {
        "offer": offer(current_price=5.50, regular_price=12.99),
        "history": (comparator(price=4.99),),
        "authoritative_atl": False,
    }
    neutral = decide(**request)
    flexible = decide(**request, constraints=PurchaseConstraints(patience="flexible"))
    assert neutral.verdict == "Buy"
    assert flexible.verdict == "Wait"
    assert flexible.objective_deal_strength == neutral.objective_deal_strength


@pytest.mark.parametrize(
    "scenario",
    [
        DecisionRequest(offer=offer(resolved=False), history=(comparator(),), authoritative_atl=True),
        DecisionRequest(offer=offer(current_price=None), history=(comparator(),), authoritative_atl=True),
        DecisionRequest(offer=offer(), history=(), authoritative_atl=None),
    ],
)
def test_each_minimum_evidence_gate_returns_insufficient_evidence(scenario):
    result = evaluate_decision(scenario)
    assert result.state == "insufficient_evidence"
    assert result.missing_requirements
    assert not hasattr(result, "verdict")


def test_authoritative_atl_without_price_history_is_a_valid_low_confidence_anchor():
    result = decide(history=(), authoritative_atl=True)
    assert result.state == "decision"
    assert result.verdict == "Buy"
    assert result.confidence == "Low"
    assert "Only one trustworthy historical anchor" in result.evidence_coverage["downgrade_reasons"][0]


@pytest.mark.parametrize("authoritative_atl", [True, False])
def test_materially_conflicting_authoritative_and_floor_evidence_abstains(authoritative_atl):
    current = 9.99 if authoritative_atl else 4.99
    historical_floor = 4.99 if authoritative_atl else 9.99
    result = decide(
        offer=offer(current_price=current),
        history=(comparator(price=historical_floor),),
        authoritative_atl=authoritative_atl,
    )
    assert result.state == "insufficient_evidence"
    assert result.conflicts


def test_confidence_labels_reflect_coverage_without_numeric_probability():
    low = decide(history=(), authoritative_atl=True)
    medium = decide(history=(comparator(), comparator(5.99, "2025-09-01", "prior_comparable")))
    deep = tuple(comparator(4.99, day, "prior_comparable") for day in (
        "2025-01-01", "2025-03-01", "2025-05-01", "2025-07-01"
    ))
    high = decide(history=deep)
    assert [low.confidence, medium.confidence, high.confidence] == ["Low", "Medium", "High"]
    assert isinstance(high.confidence, str)
    assert "probability" not in json.dumps(high.to_dict()).lower()


def test_wait_receipt_has_broad_recurrence_only_for_deep_regular_history():
    history = tuple(comparator(4.99, day, "historical_floor") for day in (
        "2025-01-01", "2025-03-01", "2025-05-01", "2025-07-01"
    ))
    result = decide(
        offer=offer(current_price=5.99, regular_price=7.99),
        history=history,
        authoritative_atl=False,
    )
    assert result.verdict == "Wait"
    assert result.recurrence["eligible"] is True
    assert "roughly every" in result.recurrence["broad_window"]
    assert "promised sale date" in result.recurrence["guidance"]


def test_irregular_deep_history_has_descriptive_recurrence_without_window():
    history = tuple(comparator(4.99, day, "historical_floor") for day in (
        "2025-01-01", "2025-01-08", "2025-06-01", "2026-01-01"
    ))
    result = decide(
        offer=offer(current_price=5.99, regular_price=7.99),
        history=history,
        authoritative_atl=False,
    )
    assert result.verdict == "Wait"
    assert result.recurrence == {
        "eligible": False,
        "observed_events": 4,
        "guidance": "Comparable sales recur irregularly, so history supports waiting but not a timing estimate.",
    }


def test_untrustworthy_history_does_not_pass_the_evidence_gate():
    result = decide(history=(comparator(trustworthy=False),), authoritative_atl=None)
    assert result.state == "insufficient_evidence"


@pytest.mark.parametrize(
    "constraints",
    [
        PurchaseConstraints(patience="forever"),
        PurchaseConstraints(intent="rental"),
        PurchaseConstraints(budget_ceiling=float("nan")),
    ],
)
def test_invalid_constraints_fail_openly(constraints):
    with pytest.raises(ValueError):
        decide(constraints=constraints)
