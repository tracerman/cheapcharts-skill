#!/usr/bin/env python3
"""
deals.py - CheapCharts deals finder with ATL signal

Lists current CheapCharts deals (sorted by latest price change by default) and
flags whether each one is at its all-time low (ATL) using the authoritative
priceHdIsLowest / priceSdIsLowest flags from DetailData.

v3.0 behavior change: the default no longer filters to ATL-only deals. The ATL
flag is shown as a column in the markdown output so you can see all current
deals plus which ones are at the historical floor. Pass --atl-only to restore
the v2.x "ATL deals only" behavior.

Coverage reality (verified 2026-06-23): iTunes has the most complete
catalog and the most stable Deals endpoint. Amazon, Vudu, and Google
Play are supported mechanically (the script accepts --store for all
four) but the underlying CheapCharts data is sparser on those stores
and the Deals endpoint is more likely to return a server-side error.
For non-iTunes stores, --title lookups work more reliably than batch
mode.

The API has no rate limits - parallel DetailData calls are safe (the
script uses 8 concurrent workers by default).

Usage:
    python deals.py                           # all current deals (iTunes), ATL flag shown
    python deals.py --title "Fight Club"      # single title lookup (always ATL-aware)
    python deals.py --store amazon            # batch on a specific store
    python deals.py --store amazon --title "Fight Club"  # single lookup on a specific store
    python deals.py --type seasons            # TV season deals instead of movies
    python deals.py --type rentalmovies       # rental movie deals
    python deals.py --sort greatestSavings    # default; sort by biggest savings
    python deals.py --genre Horror            # filter to a specific genre
    python deals.py --max-price 4.99          # only deals under $5
    python deals.py --release-year 2020-2025  # filter by release year range
    python deals.py --quality 4k             # only 4K items
    python deals.py --limit 30                # narrower deal pool
    python deals.py --min-savings 5           # only show items with $5+ savings
    python deals.py --atl-only                # filter to ATL rows only (v2.x default behavior)

Combined filters (per llms.txt guideline #11):
    python deals.py --genre Horror --max-price 4.99 --min-savings 3 --atl-only

Exit codes:
    0 - success (at least one deal returned, or single-title check completed)
    1 - no deals matched / single title not found
    2 - API error
"""

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlencode

API_BASE = "https://buster.cheapcharts.de/v1"
DEFAULT_STORE = "itunes"
DEFAULT_COUNTRY = "us"
DEFAULT_LIMIT = 80
DEFAULT_SORT = "latestPricechange"  # v3.0: time-sensitive by default; v2.x was "greatestSavings"
HTTP_TIMEOUT = 20
MAX_WORKERS = 8

# Valid sort options for Deals endpoint (per llms.txt + empirically discovered)
VALID_SORTS = (
    "latestPricechange",
    "price",
    "greatestSavings",
    "greatestPercentageSavings",
    "popularity",
    "alphabetical",
    "releaseDate",  # empirically discovered, ascending only (Pitfall #11)
)

# Valid genres (per llms.txt)
VALID_GENRES = (
    "All", "ActionAdventure", "Comedy", "Docus", "Drama", "MadeForTV",
    "Horror", "Classical", "Romance", "Independent", "KidsFamily",
    "MusicDocumentation", "SciFiFantasy", "Sport", "Thriller", "Western",
    "Anime", "Musicals",
)

# Valid quality values (per llms.txt)
VALID_QUALITIES = ("hd4k", "hd", "sd", "4k", "sdOnly")


def fetch(url):
    """Fetch URL with a User-Agent. Returns parsed JSON or raises."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def get_id_from_url(url):
    """Extract (itemType, idInStore) from cheapChartsProductPageUrl."""
    m = re.search(r"/(movies|seasons)/(\d+)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def fetch_detail(itype, sid, store=DEFAULT_STORE, country=DEFAULT_COUNTRY):
    """Hit DetailData for a single title. Returns the inner node dict.

    Also returns error messages from the API if status=error (Pitfall #15).
    """
    url = f"{API_BASE}/DetailData.php?store={store}&country={country}&itemType={itype}&idInStore={sid}"
    data = fetch(url)
    # DetailData does NOT use the standard 'status' field - it uses results.<itemType>
    # But if it returns an error, it may have status=error
    if data.get("status") == "error":
        return {"_error": data.get("message", "unknown DetailData error")}
    return data.get("results", {}).get(itype, {})


def search_id(title, store=DEFAULT_STORE, country=DEFAULT_COUNTRY):
    """Search for a title, return (itype, sid, error_msg) tuple.

    Returns (None, None, error_msg) on API error so caller can display it.
    Returns (None, None, None) if title simply not found.
    Returns (itype, sid, None) on success.
    """
    url = (
        f"{API_BASE}/gptapi/Search.php?action=search&store={store}"
        f"&country={country}&itemType=all&query={quote(title)}&limit=1"
    )
    data = fetch(url)
    if data.get("status") == "error":
        return None, None, data.get("message", "unknown Search error")
    results = data.get("results", [])
    if not results:
        return None, None, None
    itype, sid = get_id_from_url(results[0].get("cheapChartsProductPageUrl", ""))
    return itype, sid, None


def is_atl(node):
    """True if HD or SD current price is at the all-time low (authoritative flag)."""
    return node.get("priceHdIsLowest") == 1 or node.get("priceSdIsLowest") == 1


def format_atl_line(a):
    """Pretty-print one ATL item.

    Output shape (per row):
      Title | $price (was $was, save $X.XX / N%) | [Format] [HDR] | IMDb N.N | RT N% | changed YYYY-MM-DD
        buy: <apple-tv-url>
        history: <cheapcharts-url>

    The format tag (HD / 4K / SD) is the actual video quality tier - what the buyer
    gets for their money. The previous [BOTH]/[HD]/[SD] prefix was misleading: it
    signalled "this price is the floor in both HD and SD tiers" (a savings signal),
    not the format of the title. The savings signal is implicit in the ATL status;
    no need to duplicate it as a prefix.

    Ratings (IMDb, Rotten Tomatoes) are only emitted for individual movies; bundles,
    TV seasons, and TV bundles show '-' because CheapCharts doesn't carry ratings for
    those item types.
    """
    price = a.get("price")
    was = a.get("was")
    if price is not None and was is not None and was != price:
        try:
            save = float(was) - float(price)
            pct = (save / float(was) * 100) if float(was) > 0 else 0
            save_str = f" (was ${was}, save ${save:.2f} / {pct:.0f}%)"
        except (TypeError, ValueError):
            save_str = f" (was ${was})"
    else:
        save_str = ""
    # Format info: 4K / HD / SD + optional HDR tag
    fmt = a.get("format", "-")
    hdr = a.get("hdr_format")
    # Normalize hdrFormat: iTunes returns "Dolby Vision" or "No HDR" on the Deals
    # side, but DetailData may return 1/0. Handle both.
    if hdr in (1, True, "1"):
        hdr_tag = " HDR"
    elif hdr and hdr not in (0, "0", "No HDR", None):
        hdr_tag = f" {hdr}"
    else:
        hdr_tag = ""
    fmt_tag = f" [{fmt}{hdr_tag}]"
    # Ratings: only meaningful for individual movies (not bundles). Deals API
    # exposes `isMovieBundle`; Search exposes `mediaType`. We get here from Deals,
    # so use `isMovieBundle` (which is 0 / absent for individual movies, 1 / True
    # for multi-film collections).
    imdb = a.get("imdb_rating")
    rt = a.get("rotten_tomatoes_rating")
    is_bundle = a.get("is_movie_bundle")
    if not is_bundle:  # individual movie - emit ratings (or '-' if missing)
        rating_str = f" | IMDb {imdb if imdb is not None else '-'} | RT {rt if rt is not None else '-'}"
    else:
        rating_str = " | IMDb - | RT -"
    store_url = a.get("store_url")
    cc_url = a.get("url")
    url_parts = []
    if store_url:
        url_parts.append(f"buy: {store_url}")
    if cc_url:
        url_parts.append(f"history: {cc_url}")
    url_block = ("\n    " + "\n    ".join(url_parts)) if url_parts else ""
    return (
        f"  {a['title']} | ${price}{save_str}{fmt_tag}"
        f"{rating_str} | changed {a.get('change_date', '?')}"
        f"{url_block}"
    )


def check_single_title(title, store=DEFAULT_STORE, country=DEFAULT_COUNTRY):
    """Resolve a title via Search, then check DetailData for ATL status."""
    itype, sid, err = search_id(title, store, country)
    if err:
        print(f"  search error: {err}", file=sys.stderr)
        return 2
    if not sid:
        print(f"  not found: '{title}'")
        return 1
    node = fetch_detail(itype, sid, store, country)
    if not node or node.get("_error"):
        err_msg = node.get("_error", "unknown") if node else "empty response"
        print(f"  detail lookup failed for '{title}': {err_msg}", file=sys.stderr)
        return 2
    price = node.get("priceHd") or node.get("priceSd")
    print(f"  {node.get('title')}: ${price}")
    print(f"    ATL (IsLowest):      hd={node.get('priceHdIsLowest')} sd={node.get('priceSdIsLowest')}")
    print(f"    Current-sale floor (IsBest): hd={node.get('priceHdIsBest')} sd={node.get('priceSdIsBest')}")
    print(f"    Last change: {node.get('priceHdLastChangeDate') or node.get('priceSdLastChangeDate')}")
    # Store links (from DetailData)
    product_url = node.get("productPageUrl")
    itunes_url = node.get("iTunesUrl")
    cc_url = node.get("cheapChartsProductPageUrl")
    if product_url:
        print(f"    Buy on Apple TV: {product_url}")
    if itunes_url and itunes_url != product_url:
        print(f"    iTunes: {itunes_url}")
    if cc_url:
        print(f"    Price history: {cc_url}")
    if is_atl(node):
        print("    --> Currently at ATL")
    return 0


def build_deals_url(item_type, store, country, limit, sort, genre=None,
                    max_price=None, release_year=None, quality=None):
    """Build the Deals API URL with optional filters."""
    params = {
        "action": "getDeals",
        "store": store,
        "country": country,
        "itemType": item_type,
        "sort": sort,
        "limit": limit,
    }
    if genre and genre != "All":
        params["genre"] = genre
    if max_price is not None:
        params["maxPrice"] = max_price
    if release_year:
        params["releaseYear"] = release_year
    if quality and quality != "hd4k":
        params["quality"] = quality
    return f"{API_BASE}/gptapi/Deals.php?{urlencode(params)}"


def check_batch(item_type, store=DEFAULT_STORE, country=DEFAULT_COUNTRY, limit=DEFAULT_LIMIT,
                min_savings=None, output_json=False, sort=DEFAULT_SORT,
                genre=None, max_price=None, release_year=None, quality=None,
                exclude_bundles=False, atl_only=False):
    """Pull current deals with optional filters, then in parallel verify
    each via DetailData's IsLowest flag."""
    deals_url = build_deals_url(
        item_type, store, country, limit, sort, genre, max_price, release_year, quality
    )
    data = fetch(deals_url)
    if data.get("status") != "success":
        msg = data.get("message", "unknown")
        print(f"  deals fetch failed: {msg}", file=sys.stderr)
        if store != DEFAULT_STORE:
            print(f"  note: CheapCharts' Deals endpoint is most stable for iTunes. For {store},", file=sys.stderr)
            print(f"  try --title <name> for a single-title lookup, or use --store itunes for batch.", file=sys.stderr)
        return 2
    deals = data.get("results", {}).get(item_type, [])
    if not deals:
        print(f"  no {item_type} deals returned")
        return 1

    # Map each deal to (deal, itype, sid) up front
    candidates = []
    for d in deals:
        itype, sid = get_id_from_url(d.get("cheapChartsProductPageUrl", ""))
        if not (itype and sid):
            continue
        if exclude_bundles and d.get("isMovieBundle"):
            continue
        candidates.append((d, itype, sid))

    # Parallel DetailData fetches
    atl = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_deal = {
            pool.submit(fetch_detail, itype, sid, store, country): (d, itype)
            for d, itype, sid in candidates
        }
        for fut in as_completed(future_to_deal):
            d, itype = future_to_deal[fut]
            try:
                node = fut.result()
            except Exception:
                continue
            if not node or node.get("_error"):
                continue
            # v3.0: by default, include all deals (ATL flag is shown as a column).
            # --atl-only restores the v2.x behavior of dropping non-ATL rows.
            if atl_only and not is_atl(node):
                continue
            price = node.get("priceHd") or node.get("priceSd")
            was = node.get("priceHdBefore") or node.get("priceSdBefore")
            if min_savings is not None and price is not None and was is not None:
                try:
                    if float(was) - float(price) < min_savings:
                        continue
                except (TypeError, ValueError):
                    pass
            # Pre-compute savings so both JSON and markdown output have it.
            save_amount = None
            save_pct = None
            if price is not None and was is not None:
                try:
                    save_amount = float(was) - float(price)
                    save_pct = (save_amount / float(was) * 100) if float(was) > 0 else 0
                except (TypeError, ValueError):
                    pass
            atl.append({
                "title": node.get("title"),
                "price": price,
                "was": was,
                "save": save_amount,
                "save_pct": save_pct,
                "change_date": node.get("priceHdLastChangeDate") or node.get("priceSdLastChangeDate"),
                "is_atl_hd": node.get("priceHdIsLowest") == 1,
                "is_atl_sd": node.get("priceSdIsLowest") == 1,
                # Format (HD / 4K / SD) - the actual video quality tier of this title.
                # Derived from DetailData's `has4K` flag; falls back to HD if a HD price
                # exists, else SD.
                "format": "4K" if node.get("has4K") in (1, True) else (
                    "HD" if node.get("priceHd") is not None else "SD"
                ),
                "hdr_format": node.get("hdrFormat"),
                "has_atmos": node.get("hasAtmos") in (1, True),
                # CheapCharts' URL fields - the only authoritative sources for these
                # (Pitfall #32: never reconstruct from idInStore).
                "url": d.get("cheapChartsProductPageUrl"),
                "store_url": node.get("productPageUrl") or node.get("iTunesUrl"),
                # Ratings live on the Deals candidate, not DetailData. Only present for individual movies.
                "imdb_id": d.get("imdbId"),
                "imdb_rating": d.get("imdbRating"),
                "rotten_tomatoes_rating": d.get("rottenTomatoesRating"),
                "media_type": d.get("mediaType"),
                "item_type": d.get("itemType"),
                "is_movie_bundle": d.get("isMovieBundle"),
            })

    if output_json:
        print(json.dumps(atl, indent=2))
    else:
        filter_desc = []
        if genre and genre != "All":
            filter_desc.append(f"genre={genre}")
        if max_price is not None:
            filter_desc.append(f"maxPrice=${max_price}")
        if release_year:
            filter_desc.append(f"releaseYear={release_year}")
        if quality and quality != "hd4k":
            filter_desc.append(f"quality={quality}")
        if exclude_bundles:
            filter_desc.append("excludeBundles=true")
        print(format_atl_markdown(atl, item_type, len(candidates), filter_desc))
    return 0 if atl else 1


def format_atl_markdown(atl, item_type, candidates_count, filter_desc):
    """Render the ATL list as a markdown table suitable for direct inclusion in
    agent reports, READMEs, and chat output.

    Columns: Title | Fmt | Now | Was | Save | IMDb | RT | Date | ATL | Buy | History

    Title links to the Apple TV purchase page (the most likely user action). The
    ATL column shows a checkmark (✓) for titles currently at the all-time low
    across CheapCharts' tracked history, or "-" otherwise. The
    Buy and History columns are short labels that point at the Apple TV and
    CheapCharts URLs respectively. Ratings are "-" for bundles/TV seasons (CheapCharts
    doesn't carry ratings for those item types).
    """
    filter_str = f" [{', '.join(filter_desc)}]" if filter_desc else ""
    lines = [
        f"**{len(atl)} {item_type}** (out of {candidates_count} checked{filter_str})",
        "",
        "| Title | Fmt | Now | Was | Save | IMDb | RT | Date | ATL | Buy | History |",
        "|---|:-:|---:|---:|---|:-:|:-:|---|:-:|:-:|:-:|",
    ]
    for a in atl:
        title = a.get("title") or "?"
        # Title links to Apple TV (most likely user action)
        buy = a.get("store_url")
        title_cell = f"[{title}]({buy})" if buy else title
        fmt = (a.get("format") or "-").split()[0]  # "4K HDR" -> "4K"
        price = a.get("price")
        price_str = f"${price}" if price is not None else "-"
        was = a.get("was")
        was_str = f"${was}" if was is not None else "-"
        # Use pre-computed save / save_pct from the atl dict
        save_amt = a.get("save")
        save_pct = a.get("save_pct")
        if save_amt is not None and save_pct is not None:
            save_str = f"${save_amt:.2f} ({save_pct:.0f}%)"
        else:
            save_str = "-"
        # Ratings: "-" for bundles/TV (only individual movies carry them)
        if a.get("is_movie_bundle"):
            imdb_cell = rt_cell = "-"
        else:
            imdb_cell = str(a.get("imdb_rating")) if a.get("imdb_rating") is not None else "-"
            rt_cell = str(a.get("rotten_tomatoes_rating")) if a.get("rotten_tomatoes_rating") is not None else "-"
        date_cell = a.get("change_date") or "-"
        atl_cell = "✓" if (a.get("is_atl_hd") or a.get("is_atl_sd")) else "-"
        buy_cell = f"[Buy]({buy})" if buy else "-"
        hist = a.get("url")
        hist_cell = f"[History]({hist})" if hist else "-"
        # Escape any pipe chars in title (rare, but possible)
        title_cell_safe = title_cell.replace("|", "\\|")
        lines.append(
            f"| {title_cell_safe} | {fmt} | {price_str} | {was_str} | {save_str} | {imdb_cell} | {rt_cell} | {date_cell} | {atl_cell} | {buy_cell} | {hist_cell} |"
        )
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(
        description="CheapCharts All-Time-Low (ATL) checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Combined filters example: python deals.py --genre Horror --max-price 4.99 --min-savings 3"
    )
    p.add_argument("--title", help="Check a single title (resolves via Search)")
    p.add_argument("--type", choices=("buymovies", "seasons", "rentalmovies"), default="buymovies",
                   help="Item type for batch mode (default: buymovies)")
    p.add_argument("--store", default=DEFAULT_STORE, help=f"Store (default: {DEFAULT_STORE})")
    p.add_argument("--country", default=DEFAULT_COUNTRY, help=f"Country code (default: {DEFAULT_COUNTRY})")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"Deals pool size for batch mode (default: {DEFAULT_LIMIT})")
    p.add_argument("--min-savings", type=float, default=None,
                   help="Only show ATL items with at least this $ savings vs priceBefore")
    p.add_argument("--sort", choices=VALID_SORTS, default=DEFAULT_SORT,
                   help=f"Sort order for Deals (default: {DEFAULT_SORT})")
    p.add_argument("--genre", default=None,
                   help="Filter by genre (e.g. Horror, Drama, SciFiFantasy). "
                        "Only honored on itemType=buymovies (Pitfall #21, #22).")
    p.add_argument("--max-price", type=float, default=None,
                   help="Filter to deals with current price at or below this USD amount.")
    p.add_argument("--release-year", default=None,
                   help="Filter by release year or range (e.g. 2026-2026, 2024-2026).")
    p.add_argument("--quality", choices=VALID_QUALITIES, default="hd4k",
                   help="Quality filter (default: hd4k).")
    p.add_argument("--exclude-bundles", action="store_true",
                   help="Skip multi-film collections (isMovieBundle=1). Useful when you "
                        "want individual movies with ratings - bundles don't carry IMDb/RT scores.")
    p.add_argument("--atl-only", action="store_true",
                   help="Filter to ATL (all-time-low) rows only. v2.x default behavior; "
                        "v3.0 defaults to showing all deals with ATL as a column flag.")
    p.add_argument("--json", action="store_true", help="Emit JSON (batch mode only)")
    args = p.parse_args()

    try:
        if args.title:
            return check_single_title(args.title, args.store, args.country)
        if args.store == "games":
            print("  CheapCharts Games has no public API (verified 2026-06-23).", file=sys.stderr)
            print("  For current game deals, see: https://games.cheapcharts.com", file=sys.stderr)
            print("  Or use the CheapCharts Games mobile apps (iOS: id1622193150, Android: com.cheapcharts.cheapcharts_games).", file=sys.stderr)
            return 2
        return check_batch(
            item_type=args.type,
            store=args.store,
            country=args.country,
            limit=args.limit,
            min_savings=args.min_savings,
            output_json=args.json,
            sort=args.sort,
            genre=args.genre,
            max_price=args.max_price,
            release_year=args.release_year,
            quality=args.quality,
            exclude_bundles=args.exclude_bundles,
            atl_only=args.atl_only,
        )
    except Exception as e:
        print(f"  error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
