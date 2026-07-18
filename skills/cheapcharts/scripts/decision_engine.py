"""Deterministic one-title Buy / Wait / Skip decision receipts.

This module is deliberately independent of the CheapCharts network and CLI
layers.  Callers adapt a resolved offer and its historical evidence into the
dataclasses below, then render the discriminated dictionary returned by
``DecisionResult.to_dict()``.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from math import isfinite
from statistics import mean, pstdev
from typing import Any, Optional, Union


VERDICTS = ("Buy", "Wait", "Skip")
CONFIDENCE_LEVELS = ("High", "Medium", "Low")
PATIENCE_VALUES = ("balanced", "low", "flexible")
INTENT_VALUES = ("unspecified", "new_purchase", "upgrade")


@dataclass(frozen=True)
class Offer:
    """A confidently resolved, selected-format offer."""

    title: str
    title_id: str
    store: str
    country: str
    format_tier: str
    current_price: Optional[float]
    currency: str = "USD"
    regular_price: Optional[float] = None
    resolved: bool = True


@dataclass(frozen=True)
class HistoricalComparator:
    """A trustworthy historical price anchor or comparable sale event.

    ``kind`` should be ``historical_floor`` when the price is known to be the
    historical floor; otherwise ``prior_comparable`` is appropriate.  Dated
    comparable events can additionally support broad recurrence guidance.
    """

    price: float
    observed_on: Optional[str] = None
    kind: str = "prior_comparable"
    trustworthy: bool = True


@dataclass(frozen=True)
class PurchaseConstraints:
    """Optional per-request constraints. ``None`` means a visible default."""

    budget_ceiling: Optional[float] = None
    patience: Optional[str] = None
    required_format: Optional[str] = None
    intent: Optional[str] = None


@dataclass(frozen=True)
class DecisionRequest:
    offer: Offer
    history: tuple[HistoricalComparator, ...] = ()
    constraints: PurchaseConstraints = field(default_factory=PurchaseConstraints)
    authoritative_atl: Optional[bool] = None


@dataclass(frozen=True)
class DecisionReceipt:
    """A successful, discriminated decision receipt."""

    offer: dict[str, Any]
    verdict: str
    confidence: str
    decisive_reason: str
    objective_deal_strength: dict[str, Any]
    personal_fit: dict[str, Any]
    applied_constraints: dict[str, Any]
    evidence_coverage: dict[str, Any]
    decisive_evidence: tuple[str, ...]
    caveats: tuple[str, ...]
    recurrence: Optional[dict[str, Any]] = None
    state: str = field(default="decision", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InsufficientEvidenceReceipt:
    """Known facts and gaps when the hard evidence gate cannot decide."""

    offer: dict[str, Any]
    missing_requirements: tuple[str, ...]
    conflicts: tuple[str, ...]
    known_facts: tuple[str, ...]
    next_action: str
    state: str = field(default="insufficient_evidence", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DecisionResult = Union[DecisionReceipt, InsufficientEvidenceReceipt]


def _valid_price(value: Optional[float]) -> bool:
    return (
        value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value >= 0
    )


def _offer_dict(offer: Offer) -> dict[str, Any]:
    return {
        "title": offer.title,
        "title_id": offer.title_id,
        "store": offer.store,
        "country": offer.country,
        "format_tier": offer.format_tier,
        "current_price": offer.current_price,
        "currency": offer.currency,
        "regular_price": offer.regular_price,
    }


def _trusted_history(history: Sequence[HistoricalComparator]) -> list[HistoricalComparator]:
    return [item for item in history if item.trustworthy and _valid_price(item.price)]


def _historical_floor(history: Sequence[HistoricalComparator]) -> Optional[float]:
    floors = [item.price for item in history if item.kind == "historical_floor"]
    if floors:
        return min(floors)
    prices = [item.price for item in history]
    return min(prices) if prices else None


def _evidence_conflicts(
    current_price: float,
    authoritative_atl: Optional[bool],
    history: Sequence[HistoricalComparator],
) -> list[str]:
    conflicts = []
    declared_floors = [item.price for item in history if item.kind == "historical_floor"]
    if authoritative_atl is True and declared_floors and current_price > min(declared_floors) + 0.01:
        conflicts.append("authoritative ATL status conflicts with a lower trustworthy historical floor")
    if authoritative_atl is False and declared_floors and current_price <= min(declared_floors) + 0.01:
        conflicts.append("authoritative non-ATL status conflicts with the trustworthy historical floor")
    return conflicts


def _apply_constraints(constraints: PurchaseConstraints) -> dict[str, dict[str, Any]]:
    patience = constraints.patience or "balanced"
    intent = constraints.intent or "unspecified"
    if patience not in PATIENCE_VALUES:
        raise ValueError(f"patience must be one of: {', '.join(PATIENCE_VALUES)}")
    if intent not in INTENT_VALUES:
        raise ValueError(f"intent must be one of: {', '.join(INTENT_VALUES)}")
    if constraints.budget_ceiling is not None and not _valid_price(constraints.budget_ceiling):
        raise ValueError("budget_ceiling must be a finite non-negative number")

    return {
        "budget_ceiling": {
            "value": constraints.budget_ceiling,
            "source": "user_set" if constraints.budget_ceiling is not None else "default",
            "default_meaning": None if constraints.budget_ceiling is not None else "no budget ceiling applied",
        },
        "patience": {
            "value": patience,
            "source": "user_set" if constraints.patience is not None else "default",
            "default_meaning": None if constraints.patience is not None else "balanced between buying now and waiting",
        },
        "required_format": {
            "value": constraints.required_format,
            "source": "user_set" if constraints.required_format is not None else "default",
            "default_meaning": None if constraints.required_format is not None else "any offered format is acceptable",
        },
        "intent": {
            "value": intent,
            "source": "user_set" if constraints.intent is not None else "default",
            "default_meaning": None if constraints.intent is not None else "no upgrade-value penalty applied",
        },
    }


def _objective_assessment(
    offer: Offer,
    history: Sequence[HistoricalComparator],
    authoritative_atl: Optional[bool],
) -> dict[str, Any]:
    current_price = float(offer.current_price)  # hard gate already validated
    floor = _historical_floor(history)
    if authoritative_atl is True:
        position_score = 3
        position = "at_the_authoritative_floor"
        position_reason = "The selected-tier price is flagged at its authoritative all-time low."
    elif floor is not None:
        ratio = current_price / floor if floor else (1.0 if current_price == 0 else float("inf"))
        if ratio <= 1.02:
            position_score, position = 3, "at_observed_floor"
        elif ratio <= 1.20:
            position_score, position = 2, "near_observed_floor"
        elif ratio <= 1.50:
            position_score, position = 1, "above_observed_floor"
        else:
            position_score, position = 0, "well_above_observed_floor"
        position_reason = f"Current price is compared with an observed floor of {floor:.2f} {offer.currency}."
    else:
        position_score = 0
        position = "not_authoritative_atl"
        position_reason = "The authoritative ATL signal says this selected-tier price is not at its floor."

    discount_score = 0
    discount_percent = None
    if _valid_price(offer.regular_price) and offer.regular_price and offer.regular_price >= current_price:
        discount_percent = (offer.regular_price - current_price) / offer.regular_price * 100
        if discount_percent >= 50:
            discount_score = 2
        elif discount_percent >= 25:
            discount_score = 1

    dated_events = [item for item in history if item.observed_on]
    if len(dated_events) >= 4:
        sale_behavior = "repeated_comparable_sales"
        sale_behavior_reason = f"History contains {len(dated_events)} dated comparable sale events."
    elif len(history) >= 2:
        sale_behavior = "multiple_undated_or_sparse_comparators"
        sale_behavior_reason = "History contains multiple comparators but not enough dated regularity for cadence."
    elif history:
        sale_behavior = "single_historical_anchor"
        sale_behavior_reason = "Only one historical comparator describes observed sale behavior."
    else:
        sale_behavior = "atl_signal_only"
        sale_behavior_reason = "Sale behavior is not observed beyond the authoritative ATL signal."

    score = position_score + discount_score
    if score >= 3:
        label = "strong"
    elif score == 2:
        label = "fair"
    else:
        label = "weak"
    return {
        "label": label,
        "component_score": score,
        "components": {
            "price_position": {
                "assessment": position,
                "points": position_score,
                "reason": position_reason,
                "observed_floor": floor,
            },
            "discount_credibility": {
                "assessment": "verified_regular_price" if discount_percent is not None else "not_available",
                "points": discount_score,
                "discount_percent": round(discount_percent, 1) if discount_percent is not None else None,
            },
            "sale_behavior": {
                "assessment": sale_behavior,
                "points": 0,
                "reason": sale_behavior_reason,
            },
        },
    }


def _recurrence(history: Sequence[HistoricalComparator]) -> dict[str, Any]:
    dated = []
    for item in history:
        if not item.observed_on:
            continue
        try:
            dated.append(date.fromisoformat(item.observed_on[:10]))
        except (TypeError, ValueError):
            continue
    unique_dates = sorted(set(dated))
    if len(unique_dates) < 4:
        return {
            "eligible": False,
            "observed_events": len(unique_dates),
            "guidance": (
                "Comparable history is too sparse for a timing estimate; wait for another evidenced sale "
                "without assuming a date."
            ),
        }

    intervals = [(right - left).days for left, right in zip(unique_dates, unique_dates[1:])]
    average = mean(intervals)
    variability = pstdev(intervals) / average if average else float("inf")
    if variability > 0.35:
        return {
            "eligible": False,
            "observed_events": len(unique_dates),
            "guidance": "Comparable sales recur irregularly, so history supports waiting but not a timing estimate.",
        }

    low_days = max(1, round(min(intervals) * 0.85))
    high_days = max(low_days + 1, round(max(intervals) * 1.15))
    if high_days < 84:
        low_weeks = max(1, round(low_days / 7))
        high_weeks = max(low_weeks + 1, round(high_days / 7))
        broad_window = f"roughly every {low_weeks}-{high_weeks} weeks"
    else:
        low_months = max(1, round(low_days / 30))
        high_months = max(low_months + 1, round(high_days / 30))
        broad_window = f"roughly every {low_months}-{high_months} months"
    return {
        "eligible": True,
        "observed_events": len(unique_dates),
        "broad_window": broad_window,
        "interval_days": {"minimum": low_days, "maximum": high_days},
        "guidance": "This broad observed cadence is evidence-bounded, not a promised sale date or price.",
    }


def _confidence(
    offer: Offer,
    history: Sequence[HistoricalComparator],
    authoritative_atl: Optional[bool],
) -> tuple[str, dict[str, Any]]:
    dated_count = 0
    for item in history:
        try:
            if item.observed_on:
                date.fromisoformat(item.observed_on[:10])
                dated_count += 1
        except (TypeError, ValueError):
            pass

    signals = ["resolved_offer", "selected_tier_current_price", "historical_anchor"]
    missing = []
    downgrade_reasons = []
    if authoritative_atl is not None:
        signals.append("authoritative_atl_status")
    else:
        missing.append("authoritative_atl_status")
    if _valid_price(offer.regular_price):
        signals.append("regular_price")
    else:
        missing.append("regular_price")
    if dated_count >= 4:
        signals.append("deep_dated_history")
    else:
        missing.append("deep_dated_history")

    anchor_count = len(history) + (1 if authoritative_atl is not None else 0)
    if anchor_count >= 5 and authoritative_atl is not None and _valid_price(offer.regular_price) and dated_count >= 4:
        label = "High"
    elif anchor_count >= 2:
        label = "Medium"
        downgrade_reasons.append("Evidence does not cover every optional confidence signal.")
    else:
        label = "Low"
        downgrade_reasons.append("Only one trustworthy historical anchor supports the decision.")
    return label, {
        "trustworthy_historical_comparators": len(history),
        "dated_comparators": dated_count,
        "signals_present": signals,
        "missing_signals": missing,
        "conflicts": [],
        "downgrade_reasons": downgrade_reasons,
    }


def evaluate_decision(request: DecisionRequest) -> DecisionResult:
    """Evaluate one resolved offer without network, hidden state, or prediction.

    Hard evidence failures return ``insufficient_evidence``.  Otherwise the
    objective score determines the baseline action and personal constraints
    can adjust the action without changing that score.
    """

    offer = request.offer
    offer_data = _offer_dict(offer)
    trusted = _trusted_history(request.history)
    missing = []
    known = []
    if not offer.resolved or not offer.title.strip() or not offer.title_id.strip():
        missing.append("confidently resolved offer identity")
    else:
        known.append(f"resolved offer: {offer.title} ({offer.title_id})")
    if not _valid_price(offer.current_price):
        missing.append("finite non-negative current selected-tier price")
    else:
        known.append(f"current {offer.format_tier} price: {offer.current_price:.2f} {offer.currency}")
    if not trusted and request.authoritative_atl is None:
        missing.append("at least one trustworthy historical comparator or authoritative ATL status")

    conflicts = []
    if _valid_price(offer.current_price):
        conflicts = _evidence_conflicts(float(offer.current_price), request.authoritative_atl, trusted)
    if missing or conflicts:
        return InsufficientEvidenceReceipt(
            offer=offer_data,
            missing_requirements=tuple(missing),
            conflicts=tuple(conflicts),
            known_facts=tuple(known),
            next_action="Verify the missing or conflicting selected-tier price evidence, then retry this offer.",
        )

    applied = _apply_constraints(request.constraints)
    objective = _objective_assessment(offer, trusted, request.authoritative_atl)
    confidence, coverage = _confidence(offer, trusted, request.authoritative_atl)
    effects = []
    hard_conflicts = []
    budget = request.constraints.budget_ceiling
    if budget is not None and offer.current_price > budget:
        hard_conflicts.append(
            f"The current {offer.format_tier} offer costs {offer.current_price:.2f} {offer.currency}, "
            f"above the request budget of {budget:.2f}."
        )
    required_format = request.constraints.required_format
    if required_format and required_format.casefold() != offer.format_tier.casefold():
        hard_conflicts.append(
            f"The current offer is {offer.format_tier}, not the required {required_format} format."
        )

    baseline = {"strong": "Buy", "fair": "Wait", "weak": "Skip"}[objective["label"]]
    verdict = baseline
    if hard_conflicts:
        verdict = "Skip"
        effects.extend(hard_conflicts)
    elif request.constraints.intent == "upgrade" and objective["label"] != "strong":
        verdict = "Skip"
        effects.append("This offer does not clear the stronger value bar appropriate for replacing an owned copy.")
    elif request.constraints.patience == "low" and baseline == "Wait":
        verdict = "Buy"
        effects.append("Low patience makes the fair current offer preferable to waiting for a stronger price.")
    elif request.constraints.patience == "flexible" and baseline == "Buy" and request.authoritative_atl is not True:
        verdict = "Wait"
        effects.append(
            "Flexible patience favors waiting because the strong offer is not confirmed at the authoritative floor."
        )
    else:
        effects.append("No supplied personal constraint changes the objective deal-based action.")

    if verdict == "Buy":
        reason = (
            effects[0]
            if verdict != baseline
            else "The selected-tier offer clears the transparent strong-deal threshold."
        )
    elif verdict == "Wait":
        reason = (
            effects[0]
            if verdict != baseline
            else "The offer has some value, but history supports waiting for a stronger price."
        )
    else:
        if hard_conflicts or request.constraints.intent == "upgrade":
            reason = effects[0]
        else:
            reason = "This current selected-tier offer is weak relative to its observed price evidence."

    evidence = []
    price_component = objective["components"]["price_position"]
    evidence.append(price_component["reason"])
    discount = objective["components"]["discount_credibility"]
    if discount["discount_percent"] is not None:
        evidence.append(f"The current offer is {discount['discount_percent']:.1f}% below its supplied regular price.")
    evidence.extend(hard_conflicts)

    caveats = []
    if confidence == "Low":
        caveats.append("Sparse evidence makes this decision easier to change with another trustworthy comparator.")
    if request.authoritative_atl is None:
        caveats.append("No authoritative current ATL flag was supplied.")
    if verdict == "Skip":
        caveats.append("Skip applies only to this current offer and is not a judgment of the title.")

    recurrence = _recurrence(trusted) if verdict == "Wait" else None
    personal = {
        "assessment": (
            "constrained"
            if any(item["source"] == "user_set" for item in applied.values())
            else "neutral_defaults"
        ),
        "effect": "changed_action" if verdict != baseline else "no_change",
        "baseline_action": baseline,
        "effects": effects,
        "hard_constraint_conflicts": hard_conflicts,
    }
    return DecisionReceipt(
        offer=offer_data,
        verdict=verdict,
        confidence=confidence,
        decisive_reason=reason,
        objective_deal_strength=objective,
        personal_fit=personal,
        applied_constraints=applied,
        evidence_coverage=coverage,
        decisive_evidence=tuple(evidence),
        caveats=tuple(caveats),
        recurrence=recurrence,
    )
