"""Offline regression tests for live-canary decision logic."""

import importlib.util
from pathlib import Path


CANARY_PATH = Path(__file__).parents[1] / ".github" / "scripts" / "canary_pitfalls.py"
SPEC = importlib.util.spec_from_file_location("canary_pitfalls", CANARY_PATH)
assert SPEC and SPEC.loader
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


def season_response(*titles):
    return {"results": {"seasons": [{"title": title} for title in titles]}}


def test_recommendations_probe_errors_when_no_item_has_a_rating(monkeypatch):
    monkeypatch.setattr(
        canary,
        "fetch",
        lambda _url: {"results": {"buymovies": [{"title": "Unrated"}]}},
    )

    status, detail = canary.check_recommendations_rating_filter_still_ignored()

    assert status == "error"
    assert "none carried imdbRating" in detail


def test_seasons_genre_probe_retries_one_mismatch(monkeypatch):
    responses = iter([
        season_response("Horror snapshot"),
        season_response("Drama snapshot"),
        season_response("Stable snapshot"),
        season_response("Stable snapshot"),
    ])
    monkeypatch.setattr(canary, "fetch", lambda _url: next(responses))

    status, detail = canary.check_seasons_genre_still_broken()

    assert status == "ok"
    assert "after mismatch retry" in detail
