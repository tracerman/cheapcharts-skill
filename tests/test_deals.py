"""Offline unit tests for scripts/deals.py.

No network: deals.fetch is monkeypatched with recorded fixtures, so these
tests exercise the script's own logic (parsing, filtering, ordering, exit
codes, rendering) deterministically. Live-API behavior is covered by the
scheduled canary workflow instead.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import deals

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def today_utc():
    return datetime.now(timezone.utc).date()


def make_router(deals_response, detail_nodes, detail_delays=None, detail_errors=None):
    """Build a deals.fetch replacement routing by URL shape.

    detail_delays: {idInStore: seconds} to force a completion order different
    from submission order (exercises the post-fetch re-sort).
    detail_errors: set of idInStore values that should raise.
    """

    def fake_fetch(url, retries=2):
        if "Deals.php" in url:
            return deals_response
        if "DetailData.php" in url:
            sid = url.split("idInStore=")[1].split("&")[0]
            if detail_errors and sid in detail_errors:
                raise OSError(f"simulated failure for {sid}")
            if detail_delays and sid in detail_delays:
                time.sleep(detail_delays[sid])
            node = detail_nodes[sid]
            itype = url.split("itemType=")[1].split("&")[0]
            return {"results": {itype: node}}
        raise AssertionError(f"unexpected URL in test: {url}")

    return fake_fetch


# ---------------------------------------------------------------- unit tests


def test_get_id_from_url():
    assert deals.get_id_from_url("https://www.cheapcharts.com/us/itunes/movies/12345") == ("movies", "12345")
    assert deals.get_id_from_url("https://www.cheapcharts.com/us/itunes/seasons/999") == ("seasons", "999")
    assert deals.get_id_from_url("https://www.cheapcharts.com/us/itunes/other/999") == (None, None)
    assert deals.get_id_from_url("") == (None, None)


def test_normalize_genre_case_insensitive():
    assert deals.normalize_genre("horror") == "Horror"
    assert deals.normalize_genre("HORROR") == "Horror"
    assert deals.normalize_genre("scififantasy") == "SciFiFantasy"
    assert deals.normalize_genre(" Drama ") == "Drama"
    assert deals.normalize_genre("not-a-genre") is None
    assert deals.normalize_genre("") is None
    assert deals.normalize_genre(None) is None


def test_is_atl():
    assert deals.is_atl({"priceHdIsLowest": 1, "priceSdIsLowest": 0})
    assert deals.is_atl({"priceHdIsLowest": 0, "priceSdIsLowest": 1})
    assert not deals.is_atl({"priceHdIsLowest": 0, "priceSdIsLowest": 0})
    assert not deals.is_atl({})


def test_within_days():
    today = today_utc().isoformat()
    yesterday = (today_utc() - timedelta(days=1)).isoformat()
    last_week = (today_utc() - timedelta(days=7)).isoformat()
    assert deals.within_days(today, 1)
    assert not deals.within_days(yesterday, 1)
    assert deals.within_days(yesterday, 3)
    assert not deals.within_days(last_week, 3)
    assert not deals.within_days(None, 3)
    assert not deals.within_days("not-a-date", 3)
    # Datetime-ish strings: only the date prefix matters
    assert deals.within_days(f"{today} 09:30:00", 1)


def test_build_deals_url_includes_and_omits_filters():
    url = deals.build_deals_url("buymovies", "itunes", "us", 50, "latestPricechange",
                                genre="Horror", max_price=4.99, release_year="2020-2025", quality="4k")
    assert "genre=Horror" in url
    assert "maxPrice=4.99" in url
    assert "releaseYear=2020-2025" in url
    assert "quality=4k" in url
    # Defaults are omitted from the query string
    url = deals.build_deals_url("buymovies", "itunes", "us", 50, "latestPricechange",
                                genre="All", quality="hd4k")
    assert "genre" not in url
    assert "quality" not in url


def test_format_atl_markdown_escapes_pipes_and_flags_atl():
    rows = [{
        "title": "Movie | With Pipe",
        "price": 4.99, "was": 12.99, "save": 8.00, "save_pct": 61.6,
        "change_date": "2026-07-02", "is_atl_hd": True, "is_atl_sd": False,
        "format": "4K HDR", "url": "https://cc/1", "store_url": "https://tv/1",
        "imdb_rating": 8.1, "rotten_tomatoes_rating": 91, "is_movie_bundle": 0,
    }]
    out = deals.format_atl_markdown(rows, "buymovies", 10, ["genre=Horror"], failed_count=2)
    assert "**1 buymovies** (out of 10 checked, 2 lookups failed [genre=Horror])" in out
    assert "Movie \\| With Pipe" in out            # pipe escaped inside the table
    assert "| ✓ |" in out                           # ATL cell
    assert "| 4K |" in out                          # "4K HDR" collapses to first token
    assert "$8.00 (62%)" in out


def test_format_atl_markdown_bundle_hides_ratings():
    rows = [{
        "title": "Bundle", "price": 14.99, "was": 49.99, "save": 35.0, "save_pct": 70.0,
        "change_date": "2026-06-20", "is_atl_hd": False, "is_atl_sd": False,
        "format": "HD", "url": None, "store_url": None,
        "imdb_rating": 9.9, "rotten_tomatoes_rating": 99, "is_movie_bundle": 1,
    }]
    out = deals.format_atl_markdown(rows, "buymovies", 1, [])
    row_line = [ln for ln in out.splitlines() if ln.startswith("| Bundle")][0]
    cells = [c.strip() for c in row_line.split("|")]
    # Columns: Title|Fmt|Now|Was|Save|IMDb|RT|Date|ATL|Buy|History
    assert cells[6] == "-" and cells[7] == "-"      # IMDb and RT suppressed for bundles


# ------------------------------------------------------ check_batch behavior


def test_check_batch_preserves_api_order_despite_completion_order(monkeypatch, capsys):
    # First candidate finishes LAST (0.2s), last finishes first - the output
    # must still follow the Deals API order 111, 222, 333.
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"),
        load_fixture("detail_nodes.json"),
        detail_delays={"111": 0.2, "222": 0.1, "333": 0.0},
    ))
    rc = deals.check_batch("buymovies")
    out = capsys.readouterr().out
    assert rc == 0
    order = [out.index("First Movie"), out.index("Second Bundle"), out.index("Third Movie")]
    assert order == sorted(order), f"rows out of order:\n{out}"


def test_check_batch_counts_failed_lookups(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"),
        load_fixture("detail_nodes.json"),
        detail_errors={"222"},
    ))
    rc = deals.check_batch("buymovies")
    captured = capsys.readouterr()
    assert rc == 0
    assert "1 of 3 DetailData lookups failed" in captured.err
    assert "1 lookups failed" in captured.out
    assert "Second Bundle" not in captured.out


def test_check_batch_all_lookups_failed_is_api_error(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"),
        load_fixture("detail_nodes.json"),
        detail_errors={"111", "222", "333"},
    ))
    rc = deals.check_batch("buymovies")
    assert rc == 2
    assert "all DetailData lookups failed" in capsys.readouterr().err


def test_check_batch_atl_only_filters_non_atl(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"), load_fixture("detail_nodes.json"),
    ))
    rc = deals.check_batch("buymovies", atl_only=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "First Movie" in out          # priceHdIsLowest == 1
    assert "Second Bundle" not in out
    assert "Third Movie" not in out


def test_check_batch_since_filters_by_change_date(monkeypatch, capsys):
    nodes = load_fixture("detail_nodes.json")
    nodes["111"]["priceHdLastChangeDate"] = today_utc().isoformat()
    nodes["111"]["priceSdLastChangeDate"] = today_utc().isoformat()
    nodes["222"]["priceHdLastChangeDate"] = (today_utc() - timedelta(days=10)).isoformat()
    nodes["333"]["priceHdLastChangeDate"] = (today_utc() - timedelta(days=10)).isoformat()
    nodes["333"]["priceSdLastChangeDate"] = (today_utc() - timedelta(days=10)).isoformat()
    monkeypatch.setattr(deals, "fetch", make_router(load_fixture("deals_response.json"), nodes))
    rc = deals.check_batch("buymovies", since_days=1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "First Movie" in out
    assert "Second Bundle" not in out
    assert "since=1d" in out


def test_check_batch_min_savings(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"), load_fixture("detail_nodes.json"),
    ))
    # 111 saves $8.00, 222 saves $35.00, 333 saves $2.00
    rc = deals.check_batch("buymovies", min_savings=5)
    out = capsys.readouterr().out
    assert rc == 0
    assert "First Movie" in out and "Second Bundle" in out
    assert "Third Movie" not in out


def test_check_batch_exclude_bundles(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"), load_fixture("detail_nodes.json"),
    ))
    rc = deals.check_batch("buymovies", exclude_bundles=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Second Bundle" not in out


def test_check_batch_json_output(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"), load_fixture("detail_nodes.json"),
    ))
    rc = deals.check_batch("buymovies", output_json=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [d["title"] for d in data] == ["First Movie | With Pipe", "Second Bundle", "Third Movie"]
    first = data[0]
    assert first["is_atl_hd"] is True
    assert first["format"] == "4K"
    assert first["store_url"] == "https://tv.apple.com/us/movie/first-movie"


def test_check_batch_api_error_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch",
                        lambda url, retries=2: {"status": "error", "message": "boom"})
    rc = deals.check_batch("buymovies")
    assert rc == 2
    assert "boom" in capsys.readouterr().err


def test_check_batch_no_deals_exit_1(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch",
                        lambda url, retries=2: {"status": "success", "results": {"buymovies": []}})
    rc = deals.check_batch("buymovies")
    assert rc == 1


# ------------------------------------------------------------- CLI behavior


def test_main_rejects_unknown_genre(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["deals.py", "--genre", "BogusGenre"])
    rc = deals.main()
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown genre" in err
    assert "Horror" in err                           # lists the valid values


def test_main_normalizes_lowercase_genre(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"), load_fixture("detail_nodes.json"),
    ))
    monkeypatch.setattr("sys.argv", ["deals.py", "--genre", "horror"])
    rc = deals.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "genre=Horror" in out                     # CamelCase reached the API path


def test_main_store_games_is_a_clear_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["deals.py", "--store", "games"])
    rc = deals.main()
    assert rc == 2
    assert "games.cheapcharts.com" in capsys.readouterr().err


def test_fetch_retries_then_raises(monkeypatch):
    import urllib.error

    calls = {"n": 0}

    def always_fail(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("transient")

    monkeypatch.setattr(deals.time, "sleep", lambda s: None)
    monkeypatch.setattr(deals.urllib.request, "urlopen", always_fail)
    with pytest.raises(urllib.error.URLError):
        deals.fetch("https://example.invalid/x", retries=2)
    assert calls["n"] == 3                           # initial attempt + 2 retries
