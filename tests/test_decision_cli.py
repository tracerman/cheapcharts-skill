import json
import sys
from pathlib import Path

import pytest

import deals


FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name):
    with open(FIXTURES / name, encoding="utf-8") as handle:
        return json.load(handle)


def decision_router(case):
    def router(url, retries=2):
        if "Search.php" in url:
            return {"status": "success", "results": case.get("search", [])}
        if "DetailData.php" in url:
            return {"results": {"movies": case["detail"]}}
        raise AssertionError(url)

    return router


def invoke_main(monkeypatch, capsys, argv, router):
    monkeypatch.setattr(deals, "fetch", router)
    monkeypatch.setattr(sys, "argv", ["deals.py", *argv])
    rc = deals.main()
    captured = capsys.readouterr()
    return rc, captured


def test_decision_json_envelope_uses_fixture_and_visible_defaults(monkeypatch, capsys):
    case = load_json("decision_responses.json")["buy"]
    rc, captured = invoke_main(
        monkeypatch, capsys, ["--decide", "Heat", "--json"], decision_router(case)
    )

    envelope = json.loads(captured.out)
    assert rc == 0
    assert envelope["state"] == "decision"
    assert envelope["verdict"] == "Buy"
    assert envelope["offer"]["title"] == "Heat"
    assert envelope["offer"]["format_tier"] == "4K"
    assert envelope["offer"]["selected_tier"] == "hd"
    assert envelope["applied_scope"]["store"] == {"value": "itunes", "provenance": "default"}
    assert envelope["applied_scope"]["patience"] == {"value": "balanced", "provenance": "default"}
    assert envelope["personal_fit"]["assessment"] == "neutral_defaults"
    assert envelope["confidence"] in {"High", "Medium", "Low"}
    assert envelope["objective_deal_strength"]["components"]["sale_behavior"]["assessment"] == (
        "repeated_comparable_sales"
    )


def test_human_decision_receipt_matches_structured_hierarchy(monkeypatch, capsys):
    case = load_json("decision_responses.json")["buy"]
    rc, captured = invoke_main(monkeypatch, capsys, ["--decide", "Heat"], decision_router(case))

    assert rc == 0
    assert captured.out.startswith("Scope: Decide")
    assert "Title: Heat | 4K | $4.99" in captured.out
    assert "BUY —" in captured.out
    assert "Objective deal strength: Strong" in captured.out
    assert "The selected-tier price is flagged at its authoritative all-time low." in captured.out
    assert "The current offer is 66.7% below its supplied regular price." in captured.out
    assert "Personal fit: neutral defaults" in captured.out
    assert "Evidence coverage:" in captured.out
    assert "Buy link: https://tv.apple.com/us/movie/heat" in captured.out


def test_personal_budget_recalculates_without_rewriting_objective_strength(monkeypatch, capsys):
    case = load_json("decision_responses.json")["buy"]
    rc, captured = invoke_main(
        monkeypatch, capsys, ["--decide", "Heat", "--json"], decision_router(case)
    )
    baseline = json.loads(captured.out)
    assert rc == 0

    rc, captured = invoke_main(
        monkeypatch,
        capsys,
        ["--decide", "Heat", "--budget", "4", "--json"],
        decision_router(case),
    )
    constrained = json.loads(captured.out)

    assert rc == 0
    assert constrained["verdict"] == "Skip"
    assert constrained["objective_deal_strength"] == baseline["objective_deal_strength"]
    assert constrained["personal_fit"]["effect"] == "changed_action"
    assert constrained["applied_scope"]["budget_ceiling"] == {"value": 4.0, "provenance": "user_set"}
    assert any("current offer" in caveat.lower() for caveat in constrained["caveats"])


def test_wait_receipt_includes_only_supported_broad_recurrence(monkeypatch, capsys):
    case = load_json("decision_responses.json")["wait"]
    rc, captured = invoke_main(
        monkeypatch, capsys, ["--decide", "Patient Movie", "--json"], decision_router(case)
    )

    envelope = json.loads(captured.out)
    assert rc == 0
    assert envelope["verdict"] == "Wait"
    assert envelope["recurrence"]["eligible"] is True
    assert "broad_window" in envelope["recurrence"]
    assert "promised" in envelope["recurrence"]["guidance"]
    assert "next_sale_date" not in envelope["recurrence"]


def test_missing_comparator_returns_insufficient_evidence_without_verdict(monkeypatch, capsys):
    case = load_json("decision_responses.json")["insufficient"]
    rc, captured = invoke_main(
        monkeypatch, capsys, ["--decide", "Sparse Movie", "--json"], decision_router(case)
    )

    envelope = json.loads(captured.out)
    assert rc == 1
    assert envelope["state"] == "insufficient_evidence"
    assert "verdict" not in envelope
    assert envelope["offer"]["current_price"] == 9.99
    assert any("historical comparator" in item for item in envelope["missing_requirements"])


def test_trivial_drop_without_evolution_never_treats_prior_price_as_floor(monkeypatch, capsys):
    case = load_json("decision_responses.json")["trivial_drop"]
    rc, captured = invoke_main(
        monkeypatch, capsys, ["--decide", "Trivial Drop", "--json"], decision_router(case)
    )

    envelope = json.loads(captured.out)
    position = envelope["objective_deal_strength"]["components"]["price_position"]
    assert rc == 0
    assert envelope["verdict"] == "Skip"
    assert position["assessment"] == "above_unknown_historical_floor"
    assert position["points"] == 0
    assert position["observed_floor"] is None
    assert "lower historical price exists" in position["reason"]
    assert "historical floor is unknown" in position["reason"]


@pytest.mark.parametrize("required_format", ["SD", "HD"])
def test_required_format_is_minimum_and_does_not_force_lower_price_tier(
    monkeypatch, capsys, required_format
):
    case = load_json("decision_responses.json")["buy"]
    case["detail"].update({
        "priceSd": None,
        "priceSdBefore": None,
        "priceSdIsLowest": None,
    })
    rc, captured = invoke_main(
        monkeypatch,
        capsys,
        ["--decide", "Heat", "--required-format", required_format, "--json"],
        decision_router(case),
    )

    envelope = json.loads(captured.out)
    assert rc == 0
    assert envelope["verdict"] == "Buy"
    assert envelope["offer"]["selected_tier"] == "hd"
    assert envelope["offer"]["format_tier"] == "4K"
    assert not envelope["personal_fit"]["hard_constraint_conflicts"]


def test_offer_below_required_minimum_format_skips_with_offer_specific_reason(monkeypatch, capsys):
    case = load_json("decision_responses.json")["buy"]
    case["detail"]["has4K"] = 0
    rc, captured = invoke_main(
        monkeypatch,
        capsys,
        ["--decide", "Heat", "--required-format", "4K", "--json"],
        decision_router(case),
    )

    envelope = json.loads(captured.out)
    assert rc == 0
    assert envelope["verdict"] == "Skip"
    assert envelope["offer"]["format_tier"] == "HD"
    assert "below the required minimum 4K format" in envelope["decisive_reason"]


def test_ambiguous_decision_returns_candidates_and_never_fetches_detail(monkeypatch, capsys):
    case = load_json("decision_responses.json")["ambiguous"]

    def router(url, retries=2):
        if "Search.php" in url:
            return {"status": "success", "results": case["search"]}
        pytest.fail("ambiguous identity must stop before DetailData")

    rc, captured = invoke_main(monkeypatch, capsys, ["--decide", "Dune", "--json"], router)
    envelope = json.loads(captured.out)
    assert rc == 1
    assert envelope["state"] == "disambiguation"
    assert len(envelope["candidates"]) == 2
    assert "verdict" not in envelope


def test_decision_not_found_and_api_error_are_distinct_structured_states(monkeypatch, capsys):
    rc, captured = invoke_main(
        monkeypatch,
        capsys,
        ["--decide", "Missing", "--json"],
        lambda url, retries=2: {"status": "success", "results": []},
    )
    not_found = json.loads(captured.out)
    assert rc == 1
    assert not_found["state"] == "not_found"
    assert "verdict" not in not_found

    rc, captured = invoke_main(
        monkeypatch,
        capsys,
        ["--decide", "Heat", "--json"],
        lambda url, retries=2: {"status": "error", "message": "upstream unavailable"},
    )
    error = json.loads(captured.out)
    assert rc == 2
    assert error["state"] == "error"
    assert error["error"]["message"] == "upstream unavailable"
    assert "verdict" not in error


def test_unexpected_decision_failure_still_emits_error_envelope(monkeypatch, capsys):
    def fail(_url, retries=2):
        raise RuntimeError("network exploded")

    rc, captured = invoke_main(monkeypatch, capsys, ["--decide", "Heat", "--json"], fail)
    envelope = json.loads(captured.out)
    assert rc == 2
    assert envelope["state"] == "error"
    assert envelope["error"]["message"] == "network exploded"
    assert "network exploded" in captured.err


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--budget", "10"], "require --decide"),
        (["--decide", "Heat", "--scoped-json"], "Browse-only"),
        (["--decide", "Heat", "--quality", "4k"], "cannot be combined"),
        (["--json", "--scoped-json"], "alternative structured-output contracts"),
        (["--title", "Heat", "--scoped-json"], "Browse-only"),
        (["--title", "Heat", "--history", "--scoped-json"], "Browse-only"),
        (["--history", "--scoped-json"], "--history requires --title"),
    ],
)
def test_invalid_mode_combinations_fail_before_network(monkeypatch, capsys, argv, expected):
    monkeypatch.setattr(deals, "fetch", lambda *_args, **_kwargs: pytest.fail("must validate before network"))
    monkeypatch.setattr(sys, "argv", ["deals.py", *argv])

    assert deals.main() == 2
    assert expected in capsys.readouterr().err


def test_scoped_browse_json_is_additive_and_provenance_rich(monkeypatch, capsys):
    deals_response = load_json("deals_response.json")
    detail_nodes = load_json("detail_nodes.json")

    def router(url, retries=2):
        if "Deals.php" in url:
            return deals_response
        if "DetailData.php" in url:
            sid = url.rsplit("=", 1)[-1]
            return {"results": {"movies": detail_nodes[sid]}}
        raise AssertionError(url)

    rc, captured = invoke_main(
        monkeypatch,
        capsys,
        ["--scoped-json", "--genre", "Horror", "--max-price", "10"],
        router,
    )
    envelope = json.loads(captured.out)
    assert rc == 0
    assert envelope["state"] == "results"
    assert isinstance(envelope["results"], list)
    assert envelope["applied_scope"]["genre"] == {"value": "Horror", "provenance": "user_set"}
    assert envelope["applied_scope"]["sort"] == {
        "value": "latestPricechange",
        "provenance": "default",
    }
    assert envelope["result_metadata"]["canonical_identity"] == "cheapChartsProductPageUrl"


def test_scoped_browse_empty_preserves_scope_and_raw_json_stays_a_list(monkeypatch, capsys):
    empty = {"status": "success", "results": {"buymovies": []}}
    rc, captured = invoke_main(monkeypatch, capsys, ["--scoped-json", "--since", "1"], lambda *_args: empty)
    envelope = json.loads(captured.out)
    assert rc == 1
    assert envelope["state"] == "empty"
    assert envelope["results"] == []
    assert envelope["applied_scope"]["since_days"] == {"value": 1, "provenance": "user_set"}

    rc, captured = invoke_main(monkeypatch, capsys, ["--json"], lambda *_args: empty)
    assert rc == 1
    assert json.loads(captured.out) == []
    assert isinstance(json.loads(captured.out), list)
