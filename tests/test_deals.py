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


def test_select_price_tier_sd_is_coherent():
    node = load_fixture("detail_nodes.json")["333"]
    selected = deals.select_price_tier(node, "sd")
    assert selected == {
        "price": 6.99,
        "was": 8.99,
        "change_date": "2026-07-03",
        "is_atl": True,
        "tier": "sd",
    }


@pytest.mark.parametrize("quality", [None, "hd4k", "hd", "4k"])
def test_select_price_tier_hd_modes_prefer_hd_and_do_not_borrow_sd_atl(quality):
    node = load_fixture("detail_nodes.json")["333"]
    selected = deals.select_price_tier(node, quality)
    assert selected == {
        "price": 7.99,
        "was": 9.99,
        "change_date": "2026-05-01",
        "is_atl": False,
        "tier": "hd",
    }


def test_select_price_tier_hd_modes_fall_back_to_sd_when_hd_is_unavailable():
    node = {
        "priceHd": None,
        "priceSd": 2.99,
        "priceSdBefore": 4.99,
        "priceSdLastChangeDate": "2026-07-17",
        "priceSdIsLowest": 1,
    }
    selected = deals.select_price_tier(node, "4k")
    assert selected == {
        "price": 2.99,
        "was": 4.99,
        "change_date": "2026-07-17",
        "is_atl": True,
        "tier": "sd",
    }


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
    # Genre is not a supported Deals filter for seasons (Pitfall #21).
    url = deals.build_deals_url("seasons", "itunes", "us", 50, "latestPricechange",
                                genre="Horror")
    assert "genre" not in url


def test_format_atl_markdown_escapes_pipes_and_flags_atl():
    rows = [{
        "title": "Movie | With Pipe",
        "price": 4.99, "was": 12.99, "save": 8.00, "save_pct": 61.6,
        "change_date": "2026-07-02", "is_atl_hd": True, "is_atl_sd": False, "is_atl": True,
        "format": "4K HDR", "url": "https://cc/1", "store_url": "https://tv/1",
        "imdb_rating": 8.1, "rotten_tomatoes_rating": 91, "is_movie_bundle": 0,
    }]
    out = deals.format_atl_markdown(rows, "buymovies", 10, ["genre=Horror"], failed_count=2)
    assert "**1 buymovies** (out of 10 checked, 2 lookups failed [genre=Horror])" in out
    assert "Movie \\| With Pipe" in out            # pipe escaped inside the table
    assert "| ✓ |" in out                           # ATL cell
    assert "| 4K HDR |" in out                      # preserve the assembled format label
    assert "$8.00 (62%)" in out


def test_format_atl_markdown_uses_selected_tier_atl():
    rows = [{
        "title": "SD-only ATL",
        "price": 7.99, "was": 9.99, "save": 2.0, "save_pct": 20.0,
        "change_date": "2026-07-02",
        "is_atl_hd": False, "is_atl_sd": True, "selected_tier": "hd", "is_atl": False,
        "format": "HD", "url": None, "store_url": None,
        "imdb_rating": None, "rotten_tomatoes_rating": None, "is_movie_bundle": 0,
    }]
    out = deals.format_atl_markdown(rows, "buymovies", 1, [])
    row_line = next(line for line in out.splitlines() if line.startswith("| SD-only ATL"))
    cells = [cell.strip() for cell in row_line.split("|")]
    assert cells[9] == "-"


def test_format_atl_markdown_bundle_hides_ratings():
    rows = [{
        "title": "Bundle", "price": 14.99, "was": 49.99, "save": 35.0, "save_pct": 70.0,
        "change_date": "2026-06-20", "is_atl_hd": False, "is_atl_sd": False,
        "selected_tier": "hd", "is_atl": False, "format": "HD", "url": None, "store_url": None,
        "imdb_rating": 9.9, "rotten_tomatoes_rating": 99, "is_movie_bundle": 1,
    }]
    out = deals.format_atl_markdown(rows, "buymovies", 1, [])
    row_line = [ln for ln in out.splitlines() if ln.startswith("| Bundle")][0]
    cells = [c.strip() for c in row_line.split("|")]
    # Columns: Title|Fmt|Now|Was|Save|IMDb|RT|Date|ATL|Buy|History
    assert cells[6] == "-" and cells[7] == "-"      # IMDb and RT suppressed for bundles


def test_format_money_uses_symbol_or_iso_prefix():
    assert deals.format_money(4.99, "EUR") == "€4.99"
    assert deals.format_money(4.99, "GBP") == "£4.99"
    assert deals.format_money(4.99, "NZD") == "NZD 4.99"
    assert deals.format_money(None, "EUR") == "-"


def test_currency_fallback_covers_supported_countries_and_defers_to_response():
    assert deals.COUNTRY_CURRENCIES == {
        "us": "USD", "de": "EUR", "gb": "GBP", "fr": "EUR",
        "au": "AUD", "ca": "CAD", "at": "EUR", "ch": "CHF",
        "es": "EUR", "pt": "EUR", "ru": "RUB", "jp": "JPY",
        "tr": "TRY", "pl": "PLN", "in": "INR", "cn": "CNY",
    }
    assert deals.resolve_currency(None, "DE") == "EUR"
    assert deals.resolve_currency("GBP", "de") == "GBP"


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


def test_check_batch_seasons_header_omits_unapplied_genre(monkeypatch, capsys):
    season_deals = {
        "status": "success",
        "results": {"seasons": [{
            "title": "Season Deal",
            "cheapChartsProductPageUrl": "https://www.cheapcharts.com/us/itunes/seasons/111",
            "itemType": "seasons",
        }]},
    }
    router = make_router(season_deals, load_fixture("detail_nodes.json"))

    def fake_fetch(url, retries=2):
        if "Deals.php" in url:
            assert "genre=" not in url
        return router(url, retries)

    monkeypatch.setattr(deals, "fetch", fake_fetch)
    rc = deals.check_batch("seasons", genre="Horror")
    header = capsys.readouterr().out.splitlines()[0]
    assert rc == 0
    assert "genre=" not in header


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


@pytest.mark.parametrize("quality", ["sd", "sdOnly"])
def test_check_batch_sd_quality_uses_sd_tier(monkeypatch, capsys, quality):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"), load_fixture("detail_nodes.json"),
    ))
    rc = deals.check_batch("buymovies", quality=quality, output_json=True)
    rows = json.loads(capsys.readouterr().out)
    first = next(row for row in rows if row["title"] == "First Movie | With Pipe")
    third = next(row for row in rows if row["title"] == "Third Movie")
    assert rc == 0
    assert first["is_atl_hd"] is True
    assert first["is_atl_sd"] is False
    assert first["selected_tier"] == "sd"
    assert first["is_atl"] is False
    assert third["price"] == 6.99
    assert third["was"] == 8.99
    assert third["change_date"] == "2026-07-03"
    assert third["format"] == "SD"
    assert third["is_atl_hd"] is False
    assert third["is_atl_sd"] is True
    assert third["selected_tier"] == "sd"
    assert third["is_atl"] is True


def test_check_batch_hd_quality_keeps_factual_sd_atl_but_selects_hd(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"), load_fixture("detail_nodes.json"),
    ))
    rc = deals.check_batch("buymovies", quality="hd", output_json=True)
    rows = json.loads(capsys.readouterr().out)
    third = next(row for row in rows if row["title"] == "Third Movie")
    assert rc == 0
    assert third["is_atl_hd"] is False
    assert third["is_atl_sd"] is True
    assert third["selected_tier"] == "hd"
    assert third["is_atl"] is False


def test_check_batch_sd_atl_only_uses_sd_atl(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("deals_response.json"), load_fixture("detail_nodes.json"),
    ))
    rc = deals.check_batch("buymovies", quality="sd", atl_only=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "First Movie" not in out
    assert "Third Movie" in out


def test_check_batch_sd_quality_since_uses_sd_change_date(monkeypatch, capsys):
    nodes = load_fixture("detail_nodes.json")
    nodes["333"]["priceHdLastChangeDate"] = (today_utc() - timedelta(days=10)).isoformat()
    nodes["333"]["priceSdLastChangeDate"] = today_utc().isoformat()
    monkeypatch.setattr(deals, "fetch", make_router(load_fixture("deals_response.json"), nodes))
    rc = deals.check_batch("buymovies", quality="sd", since_days=1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Third Movie" in out


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


def test_check_batch_de_currency_markdown_and_json(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch", make_router(
        load_fixture("de_deals_response.json"), load_fixture("de_detail_nodes.json"),
    ))
    rc = deals.check_batch("buymovies", country="de", max_price=5.0)
    out = capsys.readouterr().out
    assert rc == 0
    assert "maxPrice=€5.0" in out
    assert "| €4.99 | €12.99 | €8.00 (62%) |" in out

    rc = deals.check_batch("buymovies", country="de", output_json=True)
    rows = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert rows[0]["currency"] == "EUR"
    assert rows[0]["price"] == 4.99


def test_check_batch_de_currency_falls_back_when_response_fields_are_missing(monkeypatch, capsys):
    deals_response = load_fixture("de_deals_response.json")
    detail_nodes = load_fixture("de_detail_nodes.json")
    for item in deals_response["results"]["buymovies"]:
        item.pop("currency", None)
    for node in detail_nodes.values():
        node.pop("currency", None)
    monkeypatch.setattr(deals, "fetch", make_router(deals_response, detail_nodes))

    rc = deals.check_batch("buymovies", country="de", max_price=5.0)
    out = capsys.readouterr().out
    assert rc == 0
    assert "maxPrice=€5.0" in out
    assert "| €4.99 | €12.99 | €8.00 (62%) |" in out

    rc = deals.check_batch("buymovies", country="de", output_json=True)
    rows = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert rows[0]["currency"] == "EUR"


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


def test_check_batch_json_api_empty_is_valid_json(monkeypatch, capsys):
    monkeypatch.setattr(deals, "fetch",
                        lambda url, retries=2: {"status": "success", "results": {"buymovies": []}})
    rc = deals.check_batch("buymovies", output_json=True)
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == "[]\n"
    assert "no buymovies deals returned" in captured.err


def test_check_batch_all_urls_malformed_is_schema_error(monkeypatch, capsys):
    malformed = load_fixture("malformed_urls_response.json")
    monkeypatch.setattr(deals, "fetch", lambda url, retries=2: malformed)
    rc = deals.check_batch("buymovies", output_json=True)
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == "[]\n"
    assert "none of 2 Deals items had a usable cheapChartsProductPageUrl" in captured.err
    assert "schema drift?" in captured.err


def test_check_batch_json_filtered_empty_stays_exit_1(monkeypatch, capsys):
    response = load_fixture("deals_response.json")
    response["results"]["buymovies"] = [response["results"]["buymovies"][1]]
    monkeypatch.setattr(deals, "fetch", make_router(response, load_fixture("detail_nodes.json")))
    rc = deals.check_batch("buymovies", exclude_bundles=True, output_json=True)
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == "[]\n"
    assert captured.err == ""


# ------------------------------------------------------------- CLI behavior


def test_main_rejects_unknown_genre(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["deals.py", "--genre", "BogusGenre"])
    rc = deals.main()
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown genre" in err
    assert "Horror" in err                           # lists the valid values


def test_main_rejects_genre_for_seasons(monkeypatch, capsys):
    monkeypatch.setattr(
        deals,
        "fetch",
        lambda *_args, **_kwargs: pytest.fail("unsupported genre must fail before API calls"),
    )
    monkeypatch.setattr("sys.argv", ["deals.py", "--type", "seasons", "--genre", "Horror"])
    rc = deals.main()
    err = capsys.readouterr().err
    assert rc == 2
    assert "--genre requires --type buymovies" in err
    assert "Pitfall #21" in err


def test_main_rejects_rentalmovies_before_api_call(monkeypatch, capsys):
    monkeypatch.setattr(
        deals,
        "fetch",
        lambda *_args, **_kwargs: pytest.fail("rental capability error must precede API calls"),
    )
    monkeypatch.setattr("sys.argv", ["deals.py", "--type", "rentalmovies"])
    rc = deals.main()
    err = capsys.readouterr().err
    assert rc == 2
    assert "--type rentalmovies is unavailable" in err
    assert "Prices silently returns purchase data" in err
    assert "Pitfall #38" in err


def test_main_rentalmovies_with_genre_preserves_genre_first_error(monkeypatch, capsys):
    monkeypatch.setattr(
        deals,
        "fetch",
        lambda *_args, **_kwargs: pytest.fail("validation errors must precede API calls"),
    )
    monkeypatch.setattr("sys.argv", [
        "deals.py", "--type", "rentalmovies", "--genre", "Horror",
    ])

    rc = deals.main()
    err = capsys.readouterr().err

    assert rc == 2
    assert "--genre requires --type buymovies" in err
    assert "Pitfall #21" in err
    assert "--type rentalmovies is unavailable" not in err


def test_main_rejects_title_rentalmovies_before_api_call(monkeypatch, capsys):
    monkeypatch.setattr(
        deals,
        "fetch",
        lambda *_args, **_kwargs: pytest.fail("title rental capability error must precede API calls"),
    )
    monkeypatch.setattr("sys.argv", [
        "deals.py", "--title", "Inception", "--type", "rentalmovies",
    ])

    rc = deals.main()
    err = capsys.readouterr().err

    assert rc == 2
    assert "--type rentalmovies is unavailable" in err
    assert "Pitfall #38" in err


def test_main_rejects_4k_quality_for_seasons_before_api_call(monkeypatch, capsys):
    monkeypatch.setattr(
        deals,
        "fetch",
        lambda *_args, **_kwargs: pytest.fail("unsupported seasons 4k must fail before API calls"),
    )
    monkeypatch.setattr("sys.argv", ["deals.py", "--type", "seasons", "--quality", "4k"])
    rc = deals.main()
    err = capsys.readouterr().err
    assert rc == 2
    assert "--quality 4k is unsupported with --type seasons" in err
    assert "hd, sd, or sdOnly" in err
    assert "Pitfall #39" in err


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


def test_main_title_defaults_do_not_warn(monkeypatch, capsys):
    monkeypatch.setattr(deals, "check_single_title", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("sys.argv", ["deals.py", "--title", "Heat"])

    assert deals.main() == 0
    assert capsys.readouterr().err == ""


def test_main_title_with_explicit_seasons_warns_and_proceeds(monkeypatch, capsys):
    calls = []

    def fake_check_single_title(*args, **kwargs):
        calls.append((args, kwargs))
        return 0

    monkeypatch.setattr(deals, "check_single_title", fake_check_single_title)
    monkeypatch.setattr("sys.argv", [
        "deals.py", "--title", "A TV Show", "--type", "seasons",
    ])

    assert deals.main() == 0
    assert len(calls) == 1
    assert "--title ignores batch-only option(s): --type" in capsys.readouterr().err


def test_main_title_warns_only_for_explicit_batch_flags(monkeypatch, capsys):
    monkeypatch.setattr(deals, "check_single_title", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("sys.argv", [
        "deals.py", "--title", "Heat", "--type", "buymovies", "--limit", "80",
        "--min-savings", "1", "--since", "1", "--sort", "latestPricechange",
        "--genre", "Horror", "--max-price", "5", "--release-year", "2020-2025",
        "--quality", "hd4k", "--exclude-bundles", "--atl-only",
    ])

    assert deals.main() == 0
    err = capsys.readouterr().err
    for option in deals.TITLE_BATCH_ONLY_OPTIONS:
        assert option in err


def test_main_title_rejects_json(monkeypatch, capsys):
    monkeypatch.setattr(
        deals,
        "check_single_title",
        lambda *_args, **_kwargs: pytest.fail("--json must fail before title lookup"),
    )
    monkeypatch.setattr("sys.argv", ["deals.py", "--title=Heat", "--json"])

    assert deals.main() == 2
    assert "--json is batch-only" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--since", "0"],
    ["--limit", "-1"],
    ["--max-price", "0"],
    ["--min-savings", "nan"],
    ["--release-year", "202"],
    ["--release-year", "2025-2020"],
])
def test_main_rejects_invalid_numeric_and_release_year_domains(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["deals.py", *argv])

    with pytest.raises(SystemExit) as exc_info:
        deals.main()

    assert exc_info.value.code == 2


# ------------------------------------------------- price history / evolution

TJ_KIDS_EVOLUTION = ("2026-07-01:+44.99~2026-06-26:-14.99~2026-06-01:-29.99~2026-01-21:+44.99"
                     "~2026-01-12:-19.99~2026-01-06:+44.99~2025-12-19:-29.99~2024-12-03:+44.99"
                     "~2024-11-21:-19.99~2022-06-22:+44.99~2022-05-16:-29.99~2021-11-30:+44.99"
                     "~2021-11-19:-14.99~2021-02-23:44.99")
BERNIE_EVOLUTION = ("2026-06-23:-4.99~2026-05-06:+12.99~2026-05-01:-5.99~2026-04-14:+12.99"
                    "~2026-04-10:-4.99~2026-03-20:+12.99~2026-03-13:-8.99~2026-02-17:12.99")


def test_parse_evolution_absolute_prices():
    entries = deals.parse_evolution(TJ_KIDS_EVOLUTION)
    assert len(entries) == 14
    # Newest first; each value is the ABSOLUTE price, sign is direction only
    assert entries[0] == ("2026-07-01", "+", 44.99)
    assert entries[-1] == ("2021-02-23", "", 44.99)     # initial listing, no sign
    # Newest segment equals the live current price (the consistency invariant)
    assert deals.parse_evolution(BERNIE_EVOLUTION)[0][2] == 4.99


def test_parse_evolution_skips_malformed():
    assert deals.parse_evolution("garbage~2026-01-01:-4.99~also:bad") == [("2026-01-01", "-", 4.99)]
    assert deals.parse_evolution("") == []
    assert deals.parse_evolution(None) == []


def test_format_history_lines_marks_floor():
    lines = deals.format_history_lines(TJ_KIDS_EVOLUTION, "SD")
    assert lines[0] == "    SD price history (14 changes, floor $14.99):"
    floor_lines = [ln for ln in lines if "historical floor" in ln]
    assert len(floor_lines) == 2                        # Nov 2021 and Jun 2026
    assert "2021-11-19 -> 2021-11-30: dropped to $14.99" in floor_lines[0]
    assert lines[-1].endswith("rose to $44.99")         # newest row ends at "now"
    assert "-> now:" in lines[-1]
    assert deals.format_history_lines(None, "HD") == []


def test_format_history_lines_marks_unsigned_initial_listing_floor():
    lines = deals.format_history_lines("2026-02-01:+9.99~2026-01-01:4.99", "HD")

    assert "listed at $4.99  <-- historical floor" in lines[1]


def test_single_title_de_currency_and_history(monkeypatch, capsys):
    detail = load_fixture("de_detail_nodes.json")["444"]

    def router(url, retries=2):
        if "Search.php" in url:
            return {
                "status": "success",
                "results": [{
                    "title": "German Movie",
                    "cheapChartsProductPageUrl":
                        "https://www.cheapcharts.com/de/itunes/movies/444",
                }],
            }
        if "DetailData.php" in url:
            return {"results": {"movies": detail}}
        raise AssertionError(url)

    monkeypatch.setattr(deals, "fetch", router)
    rc = deals.check_single_title("German Movie", country="de", show_history=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "German Movie: €4.99" in out
    assert "HD price history (2 changes, floor €4.99)" in out
    assert "dropped to €4.99" in out


def test_single_title_de_currency_falls_back_for_current_price_and_history(monkeypatch, capsys):
    detail = load_fixture("de_detail_nodes.json")["444"]
    detail.pop("currency", None)

    def router(url, retries=2):
        if "Search.php" in url:
            return {
                "status": "success",
                "results": [{
                    "title": "German Movie",
                    "cheapChartsProductPageUrl":
                        "https://www.cheapcharts.com/de/itunes/movies/444",
                }],
            }
        if "DetailData.php" in url:
            return {"results": {"movies": detail}}
        raise AssertionError(url)

    monkeypatch.setattr(deals, "fetch", router)
    rc = deals.check_single_title("German Movie", country="de", show_history=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "German Movie: €4.99" in out
    assert "HD price history (2 changes, floor €4.99)" in out
    assert "dropped to €4.99" in out


def test_single_title_reports_price_unavailable_when_both_tiers_are_missing(monkeypatch, capsys):
    monkeypatch.setattr(deals, "search_id", lambda *_args: ("movies", "123", None))
    monkeypatch.setattr(deals, "fetch_detail", lambda *_args: {
        "title": "Unavailable Movie",
        "priceHd": None,
        "priceSd": None,
    })

    rc = deals.check_single_title("Unavailable Movie")
    out = capsys.readouterr().out

    assert rc == 0
    assert "Unavailable Movie: price unavailable" in out


# ------------------------------------------------------- search title match


def test_score_match_prefers_franchise_over_short_substring():
    query = "Tom and Jerry Kids"
    show = "Tom & Jerry Kids Show: The Complete Series"
    movie = "Tom & Jerry"                               # the unrelated 2021 movie
    assert deals.score_match(query, show) > deals.score_match(query, movie)


def test_score_match_exact_wins():
    assert deals.score_match("Bernie", "Bernie") > deals.score_match("Bernie", "Bernie Mac Show")
    assert deals.score_match("", "anything") == 0.0


def search_response(items):
    return {"status": "success", "results": items}


def test_search_id_picks_best_match_not_first_hit(monkeypatch):
    hits = [
        {"title": "Tom & Jerry",
         "cheapChartsProductPageUrl": "https://www.cheapcharts.com/us/itunes/movies/1553452603"},
        {"title": "Tom & Jerry Kids Show: The Complete Series",
         "cheapChartsProductPageUrl": "https://www.cheapcharts.com/us/itunes/seasons/1550380051"},
    ]
    monkeypatch.setattr(deals, "fetch", lambda url, retries=2: search_response(hits))
    itype, sid, err = deals.search_id("Tom and Jerry Kids Complete Series")
    assert (itype, sid, err) == ("seasons", "1550380051", None)


def test_search_id_skips_candidates_without_usable_url(monkeypatch):
    hits = [
        {"title": "Perfect Match", "cheapChartsProductPageUrl": "https://www.cheapcharts.com/us/other/1"},
        {"title": "Perfect Match Extended", "cheapChartsProductPageUrl":
            "https://www.cheapcharts.com/us/itunes/movies/42"},
    ]
    monkeypatch.setattr(deals, "fetch", lambda url, retries=2: search_response(hits))
    itype, sid, err = deals.search_id("Perfect Match")
    assert (itype, sid) == ("movies", "42")


def test_search_id_warns_on_weak_match(monkeypatch, capsys):
    hits = [
        {"title": "Completely Unrelated Documentary",
         "cheapChartsProductPageUrl": "https://www.cheapcharts.com/us/itunes/movies/7"},
        {"title": "Also Wrong", "cheapChartsProductPageUrl": "https://www.cheapcharts.com/us/itunes/movies/8"},
    ]
    monkeypatch.setattr(deals, "fetch", lambda url, retries=2: search_response(hits))
    itype, sid, err = deals.search_id("Zyzzyva Quantum Horizons")
    err_out = capsys.readouterr().err
    assert sid is not None                              # still returns the best guess
    assert "weak title match" in err_out
    assert "other search hits" in err_out


def test_single_title_history_and_na_rendering(monkeypatch, capsys):
    def router(url, retries=2):
        if "Search.php" in url:
            return search_response([{
                "title": "Tom & Jerry Kids Show: The Complete Series",
                "cheapChartsProductPageUrl": "https://www.cheapcharts.com/us/itunes/seasons/1550380051",
            }])
        if "DetailData.php" in url:
            return {"results": {"seasons": {
                "title": "Tom & Jerry Kids Show: The Complete Series",
                "priceSd": 44.99, "priceHd": None,
                "priceSdIsLowest": 0, "priceHdIsLowest": None,
                "priceSdIsBest": 0, "priceHdIsBest": None,
                "priceSdLastChangeDate": "2026-07-01",
                "priceSdEvolution": TJ_KIDS_EVOLUTION, "priceHdEvolution": None,
            }}}
        raise AssertionError(url)

    monkeypatch.setattr(deals, "fetch", router)
    rc = deals.check_single_title("Tom & Jerry Kids Complete Series", show_history=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "hd=n/a" in out                              # no raw None in output
    assert "SD price history (14 changes, floor $14.99)" in out
    assert out.count("historical floor") == 2


def test_main_history_requires_title(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["deals.py", "--history"])
    rc = deals.main()
    assert rc == 2
    assert "--history requires --title" in capsys.readouterr().err


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
