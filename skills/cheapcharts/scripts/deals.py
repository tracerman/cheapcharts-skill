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
    python deals.py --title "Fight Club" --history   # + full tracked price history timeline
    python deals.py --store amazon            # batch on a specific store
    python deals.py --store amazon --title "Fight Club"  # single lookup on a specific store
    python deals.py --type seasons            # TV season deals instead of movies
    python deals.py --type rentalmovies       # exits 2: public API rental prices are unavailable
    python deals.py --sort greatestSavings    # sort by biggest savings (default: latestPricechange)
    python deals.py --genre Horror            # filter to a specific genre (case-insensitive)
    python deals.py --max-price 4.99          # only deals under $5
    python deals.py --release-year 2020-2025  # filter by release year range
    python deals.py --quality 4k             # only 4K movies (not supported for seasons)
    python deals.py --limit 30                # narrower deal pool
    python deals.py --min-savings 5           # only show items with $5+ savings
    python deals.py --since 1                 # only items whose price changed in the last day
    python deals.py --atl-only                # filter to ATL rows only (v2.x default behavior)

Combined filters (per llms.txt guideline #11):
    python deals.py --genre Horror --max-price 4.99 --min-savings 3 --atl-only

Exit codes:
    0 - success (at least one deal returned, or single-title check completed)
    1 - no deals matched / single title not found
    2 - API, usage, or response-schema error
"""

import argparse
import difflib
import json
import math
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

# The markdown table uses non-ASCII glyphs (the ATL checkmark). Windows consoles
# and pipes default to cp1252, which cannot encode them and kills the whole run
# with UnicodeEncodeError - force UTF-8 wherever the runtime allows it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

API_BASE = "https://buster.cheapcharts.de/v1"
DEFAULT_STORE = "itunes"
DEFAULT_COUNTRY = "us"
DEFAULT_LIMIT = 80
DEFAULT_SORT = "latestPricechange"  # v3.0: time-sensitive by default; v2.x was "greatestSavings"
HTTP_TIMEOUT = 20
MAX_WORKERS = 8

CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "AUD": "A$",
    "CAD": "C$",
    "CHF": "CHF ",
    "RUB": "₽",
    "TRY": "₺",
    "PLN": "PLN ",
    "INR": "₹",
    "CNY": "CN¥",
}

COUNTRY_CURRENCIES = {
    "us": "USD",
    "de": "EUR",
    "gb": "GBP",
    "fr": "EUR",
    "au": "AUD",
    "ca": "CAD",
    "at": "EUR",
    "ch": "CHF",
    "es": "EUR",
    "pt": "EUR",
    "ru": "RUB",
    "jp": "JPY",
    "tr": "TRY",
    "pl": "PLN",
    "in": "INR",
    "cn": "CNY",
}

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

TITLE_BATCH_ONLY_OPTIONS = (
    "--type",
    "--limit",
    "--min-savings",
    "--since",
    "--sort",
    "--genre",
    "--max-price",
    "--release-year",
    "--quality",
    "--exclude-bundles",
    "--atl-only",
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


def fetch(url, retries=2):
    """Fetch URL with a User-Agent, retrying transient failures with backoff.

    Returns parsed JSON or raises the last error after `retries` retries.
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, socket.timeout, TimeoutError, ValueError) as e:
            # socket.timeout is only an alias of TimeoutError from 3.10; listed
            # separately so read-timeouts retry on Python 3.9 too.
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last_err


def normalize_genre(genre):
    """Map a case-insensitive genre name to the API's CamelCase enum value.

    Returns None for unknown genres. This matters because the API does NOT
    error on unknown genre values - it silently returns the unfiltered list
    (Pitfall: genre falls back to All), so 'horror' instead of 'Horror' would
    quietly give the user everything.
    """
    if not genre:
        return None
    return {g.lower(): g for g in VALID_GENRES}.get(genre.strip().lower())


def within_days(date_str, days):
    """True if date_str (YYYY-MM-DD...) falls within the last `days` days (UTC).

    --since 1 means "changed today"; --since 3 means "changed in the last 3 days".
    Unparseable or missing dates return False.
    """
    if not date_str:
        return False
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return (datetime.now(timezone.utc).date() - d).days < days


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


def _norm_title(s):
    """Normalize a title for matching: lowercase, '&' -> 'and', strip punctuation."""
    s = (s or "").lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", s))


def score_match(query, title):
    """Similarity score between a user query and a catalog title (0..~1.05).

    Blends character similarity, token containment (does the title cover the
    query's words?), and a bonus when the normalized query appears inside the
    title. The containment direction is deliberately asymmetric: 'Tom and
    Jerry Kids' should prefer 'Tom & Jerry Kids Show: The Complete Series'
    over the shorter movie 'Tom & Jerry' that merely fits inside the query.
    """
    q, t = _norm_title(query), _norm_title(title)
    if not q or not t:
        return 0.0
    ratio = difflib.SequenceMatcher(None, q, t).ratio()
    q_tokens, t_tokens = set(q.split()), set(t.split())
    containment = len(q_tokens & t_tokens) / len(q_tokens)
    substr = 0.25 if q in t else 0.0
    return 0.45 * ratio + 0.35 * containment + substr


def search_id(title, store=DEFAULT_STORE, country=DEFAULT_COUNTRY):
    """Search for a title, return (itype, sid, error_msg) tuple.

    Fetches several candidates and picks the best title match instead of
    blindly trusting the API's first hit - Search ranking is fragile ('Tom
    and Jerry Kids' top-ranks the unrelated 2021 movie 'Tom & Jerry').
    Warns on stderr when the best match is weak, listing the alternatives.

    Returns (None, None, error_msg) on API error so caller can display it.
    Returns (None, None, None) if title simply not found.
    Returns (itype, sid, None) on success.
    """
    url = (
        f"{API_BASE}/gptapi/Search.php?action=search&store={store}"
        f"&country={country}&itemType=all&query={quote(title)}&limit=5"
    )
    data = fetch(url)
    if data.get("status") == "error":
        return None, None, data.get("message", "unknown Search error")
    results = data.get("results", [])
    if not results:
        return None, None, None
    ranked = sorted(results, key=lambda r: score_match(title, r.get("title")), reverse=True)
    for candidate in ranked:
        itype, sid = get_id_from_url(candidate.get("cheapChartsProductPageUrl", ""))
        if itype and sid:
            best_score = score_match(title, candidate.get("title"))
            if best_score < 0.6 and len(ranked) > 1:
                others = "; ".join(r.get("title") or "?" for r in ranked if r is not candidate)[:200]
                print(f"  warning: weak title match for '{title}' - using '{candidate.get('title')}'",
                      file=sys.stderr)
                print(f"  other search hits: {others}", file=sys.stderr)
            return itype, sid, None
    return None, None, None


def parse_evolution(evo):
    """Parse a priceHd/SdEvolution string into [(date, direction, price)], newest first.

    Format: `YYYY-MM-DD:[+|-]price~...`. Each value is the ABSOLUTE price in
    effect from that date - the sign only marks the change direction ('+' rose,
    '-' dropped, no sign = initial tracked price, rightmost segment). They are
    NOT deltas; summing them produces garbage (see Pitfall #26). Verified
    2026-07-02 against live data: the newest segment always equals the current
    priceHd/priceSd. Malformed segments are skipped.
    """
    entries = []
    for seg in (evo or "").split("~"):
        m = re.match(r"^(\d{4}-\d{2}-\d{2}):([+-]?)(\d+(?:\.\d+)?)$", seg.strip())
        if m:
            entries.append((m.group(1), m.group(2), float(m.group(3))))
    return entries


def format_money(value, currency, format_spec=""):
    """Format a numeric amount with a known symbol or an ISO-code prefix."""
    if value is None:
        return "-"
    code = (currency or "USD").upper()
    prefix = CURRENCY_SYMBOLS.get(code, f"{code} ")
    amount = format(value, format_spec) if format_spec else str(value)
    return f"{prefix}{amount}"


def positive_int(value):
    """Argparse type for integer options whose valid domain starts at one."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def positive_float(value):
    """Argparse type for finite numeric options whose valid domain is greater than zero."""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def release_year_value(value):
    """Argparse type for YYYY or ordered YYYY-YYYY ranges."""
    match = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", value)
    if not match:
        raise argparse.ArgumentTypeError("must be YYYY or YYYY-YYYY")
    start, end = int(match.group(1)), int(match.group(2) or match.group(1))
    if start > end:
        raise argparse.ArgumentTypeError("range start must not exceed range end")
    return value


def supplied_long_options(argv):
    """Return explicitly supplied --long-option names, including --name=value forms."""
    return {token.split("=", 1)[0] for token in argv if token.startswith("--")}


def resolve_currency(response_currency, country=DEFAULT_COUNTRY):
    """Prefer a priced-response currency; otherwise use the supported-country fallback."""
    if response_currency:
        return str(response_currency).upper()
    return COUNTRY_CURRENCIES.get((country or DEFAULT_COUNTRY).lower(), "USD")


def format_history_lines(evo, label, currency="USD"):
    """Render one tier's evolution string as indented timeline lines, oldest first.

    Marks the historical floor rows so "when was it cheapest" is answerable at
    a glance. Returns [] when there is no parseable history for this tier.
    """
    entries = parse_evolution(evo)
    if not entries:
        return []
    chron = list(reversed(entries))
    floor = min(price for _, _, price in chron)
    lines = [
        f"    {label} price history "
        f"({len(chron)} changes, floor {format_money(floor, currency, 'g')}):"
    ]
    for i, (date, sign, price) in enumerate(chron):
        end = chron[i + 1][0] if i + 1 < len(chron) else "now"
        event = {"": "listed at", "+": "rose to", "-": "dropped to"}[sign]
        floor_tag = "  <-- historical floor" if price == floor else ""
        lines.append(
            f"      {date} -> {end}: {event} "
            f"{format_money(price, currency, 'g')}{floor_tag}"
        )
    return lines


def is_atl(node):
    """True if HD or SD current price is at the all-time low (authoritative flag)."""
    return node.get("priceHdIsLowest") == 1 or node.get("priceSdIsLowest") == 1


def check_single_title(title, store=DEFAULT_STORE, country=DEFAULT_COUNTRY, show_history=False):
    """Resolve a title via Search, then check DetailData for ATL status.

    With show_history, also renders the tracked price-history timeline parsed
    from priceHdEvolution / priceSdEvolution (absolute prices, Pitfall #26).
    """
    def flag(v):
        # SD-only titles have no HD tier at all - render n/a, not a raw None
        return "n/a" if v is None else v

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
    price = node.get("priceHd")
    if price is None:
        price = node.get("priceSd")
    currency = resolve_currency(node.get("currency"), country)
    price_text = format_money(price, currency) if price is not None else "price unavailable"
    print(f"  {node.get('title')}: {price_text}")
    print(f"    ATL (IsLowest):      hd={flag(node.get('priceHdIsLowest'))} sd={flag(node.get('priceSdIsLowest'))}")
    print(f"    Current-sale floor (IsBest): hd={flag(node.get('priceHdIsBest'))} sd={flag(node.get('priceSdIsBest'))}")
    print(f"    Last change: {node.get('priceHdLastChangeDate') or node.get('priceSdLastChangeDate')}")
    if show_history:
        history = (format_history_lines(node.get("priceHdEvolution"), "HD", currency)
                   + format_history_lines(node.get("priceSdEvolution"), "SD", currency))
        if history:
            print("\n".join(history))
        else:
            print("    no tracked price history for this title")
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
    if item_type == "buymovies" and genre and genre != "All":
        params["genre"] = genre
    if max_price is not None:
        params["maxPrice"] = max_price
    if release_year:
        params["releaseYear"] = release_year
    if quality and quality != "hd4k":
        params["quality"] = quality
    return f"{API_BASE}/gptapi/Deals.php?{urlencode(params)}"


def select_price_tier(node, quality):
    """Return one coherent DetailData price tier for the requested quality.

    SD modes never borrow HD fields. Other modes prefer HD and fall back to SD
    only when the HD tier is unavailable.
    """
    tier = "Sd" if quality in ("sd", "sdOnly") or node.get("priceHd") is None else "Hd"
    return {
        "price": node.get(f"price{tier}"),
        "was": node.get(f"price{tier}Before"),
        "change_date": node.get(f"price{tier}LastChangeDate"),
        "is_atl": node.get(f"price{tier}IsLowest") == 1,
        "tier": tier.lower(),
    }


def check_batch(item_type, store=DEFAULT_STORE, country=DEFAULT_COUNTRY, limit=DEFAULT_LIMIT,
                min_savings=None, output_json=False, sort=DEFAULT_SORT,
                genre=None, max_price=None, release_year=None, quality=None,
                exclude_bundles=False, atl_only=False, since_days=None):
    """Pull current deals with optional filters, then in parallel verify
    each via DetailData's IsLowest flag."""
    deals_url = build_deals_url(
        item_type, store, country, limit, sort, genre, max_price, release_year, quality
    )
    sent_params = dict(parse_qsl(urlsplit(deals_url).query))
    data = fetch(deals_url)
    if data.get("status") != "success":
        msg = data.get("message", "unknown")
        print(f"  deals fetch failed: {msg}", file=sys.stderr)
        if store != DEFAULT_STORE:
            print(f"  note: CheapCharts' Deals endpoint is most stable for iTunes. For {store},", file=sys.stderr)
            print("  try --title <name> for a single-title lookup, or use --store itunes for batch.", file=sys.stderr)
        return 2
    deals = data.get("results", {}).get(item_type, [])
    if not deals:
        if output_json:
            print("[]")
            print(f"  no {item_type} deals returned", file=sys.stderr)
        else:
            print(f"  no {item_type} deals returned")
        return 1
    response_currency = next((d.get("currency") for d in deals if d.get("currency")), None)
    request_currency = resolve_currency(response_currency, country)

    # Map each deal to (deal, itype, sid) up front
    candidates = []
    unresolvable = 0
    for d in deals:
        itype, sid = get_id_from_url(d.get("cheapChartsProductPageUrl", ""))
        if not (itype and sid):
            unresolvable += 1
            continue
        if exclude_bundles and d.get("isMovieBundle"):
            continue
        candidates.append((d, itype, sid))
    if unresolvable == len(deals):
        if output_json:
            print("[]")
        print(f"  none of {len(deals)} Deals items had a usable cheapChartsProductPageUrl "
              "- response URL schema drift?", file=sys.stderr)
        return 2

    # Parallel DetailData fetches. as_completed yields in COMPLETION order, so
    # rows carry their candidate index and get re-sorted afterwards - otherwise
    # the output would scramble the Deals API's sort order.
    rows = []
    failed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_deal = {
            pool.submit(fetch_detail, itype, sid, store, country): (idx, d, itype)
            for idx, (d, itype, sid) in enumerate(candidates)
        }
        for fut in as_completed(future_to_deal):
            idx, d, itype = future_to_deal[fut]
            try:
                node = fut.result()
            except Exception:
                failed += 1
                continue
            if not node or node.get("_error"):
                failed += 1
                continue
            selected = select_price_tier(node, quality)
            # v3.0: by default, include all deals (ATL flag is shown as a column).
            # --atl-only restores the v2.x behavior for the selected price tier.
            if atl_only and not selected["is_atl"]:
                continue
            change_date = selected["change_date"]
            if since_days is not None and not within_days(change_date, since_days):
                continue
            price = selected["price"]
            was = selected["was"]
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
            rows.append((idx, {
                "title": node.get("title"),
                "price": price,
                "was": was,
                "save": save_amount,
                "save_pct": save_pct,
                "currency": resolve_currency(node.get("currency") or d.get("currency"), country),
                "change_date": change_date,
                "is_atl_hd": node.get("priceHdIsLowest") == 1,
                "is_atl_sd": node.get("priceSdIsLowest") == 1,
                "selected_tier": selected["tier"],
                "is_atl": selected["is_atl"],
                # Format follows the selected price tier. Within the HD tier,
                # DetailData's has4K flag distinguishes 4K from ordinary HD.
                "format": "SD" if selected["tier"] == "sd" else (
                    "4K" if node.get("has4K") in (1, True) else "HD"
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
            }))

    # Restore the Deals API's sort order.
    rows.sort(key=lambda pair: pair[0])
    atl = [row for _, row in rows]

    if failed:
        print(f"  warning: {failed} of {len(candidates)} DetailData lookups failed - "
              f"results may be incomplete", file=sys.stderr)
    if failed and failed == len(candidates):
        print("  all DetailData lookups failed - treating as API error", file=sys.stderr)
        return 2

    if output_json:
        print(json.dumps(atl, indent=2))
    else:
        filter_desc = []
        if "genre" in sent_params:
            filter_desc.append(f"genre={sent_params['genre']}")
        if "maxPrice" in sent_params:
            filter_desc.append(
                f"maxPrice={format_money(sent_params['maxPrice'], request_currency)}"
            )
        if "releaseYear" in sent_params:
            filter_desc.append(f"releaseYear={sent_params['releaseYear']}")
        if "quality" in sent_params:
            filter_desc.append(f"quality={sent_params['quality']}")
        if since_days is not None:
            filter_desc.append(f"since={since_days}d")
        if exclude_bundles:
            filter_desc.append("excludeBundles=true")
        print(format_atl_markdown(atl, item_type, len(candidates), filter_desc, failed_count=failed))
    return 0 if atl else 1


def format_atl_markdown(atl, item_type, candidates_count, filter_desc, failed_count=0):
    """Render the ATL list as a markdown table suitable for direct inclusion in
    agent reports, READMEs, and chat output.

    Columns: Title | Fmt | Now | Was | Save | IMDb | RT | Date | ATL | Buy | History

    Title links to the Apple TV purchase page (the most likely user action). The
    ATL column shows a checkmark (✓) when the selected price tier is currently
    at the all-time low across CheapCharts' tracked history, or "-" otherwise. The
    Buy and History columns are short labels that point at the Apple TV and
    CheapCharts URLs respectively. Ratings are "-" for bundles/TV seasons (CheapCharts
    doesn't carry ratings for those item types).
    """
    filter_str = f" [{', '.join(filter_desc)}]" if filter_desc else ""
    failed_str = f", {failed_count} lookups failed" if failed_count else ""
    lines = [
        f"**{len(atl)} {item_type}** (out of {candidates_count} checked{failed_str}{filter_str})",
        "",
        "| Title | Fmt | Now | Was | Save | IMDb | RT | Date | ATL | Buy | History |",
        "|---|:-:|---:|---:|---|:-:|:-:|---|:-:|:-:|:-:|",
    ]
    for a in atl:
        title = a.get("title") or "?"
        # Title links to Apple TV (most likely user action)
        buy = a.get("store_url")
        title_cell = f"[{title}]({buy})" if buy else title
        fmt = a.get("format") or "-"
        price = a.get("price")
        currency = a.get("currency") or "USD"
        price_str = format_money(price, currency)
        was = a.get("was")
        was_str = format_money(was, currency)
        # Use pre-computed save / save_pct from the atl dict
        save_amt = a.get("save")
        save_pct = a.get("save_pct")
        if save_amt is not None and save_pct is not None:
            save_str = f"{format_money(save_amt, currency, '.2f')} ({save_pct:.0f}%)"
        else:
            save_str = "-"
        # Ratings: "-" for bundles/TV (only individual movies carry them)
        if a.get("is_movie_bundle"):
            imdb_cell = rt_cell = "-"
        else:
            imdb_cell = str(a.get("imdb_rating")) if a.get("imdb_rating") is not None else "-"
            rt_cell = str(a.get("rotten_tomatoes_rating")) if a.get("rotten_tomatoes_rating") is not None else "-"
        date_cell = a.get("change_date") or "-"
        atl_cell = "✓" if a.get("is_atl") else "-"
        buy_cell = f"[Buy]({buy})" if buy else "-"
        hist = a.get("url")
        hist_cell = f"[History]({hist})" if hist else "-"
        # Escape any pipe chars in title (rare, but possible)
        title_cell_safe = title_cell.replace("|", "\\|")
        lines.append(
            f"| {title_cell_safe} | {fmt} | {price_str} | {was_str} | {save_str} "
            f"| {imdb_cell} | {rt_cell} | {date_cell} | {atl_cell} | {buy_cell} | {hist_cell} |"
        )
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(
        description="CheapCharts All-Time-Low (ATL) checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Combined filters example: python deals.py --genre Horror --max-price 4.99 --min-savings 3"
    )
    p.add_argument("--title", help="Check a single title (resolves via Search)")
    p.add_argument("--history", action="store_true",
                   help="With --title: also print the tracked price-history timeline "
                        "(sale windows, historical floor) parsed from the evolution data")
    p.add_argument("--type", choices=("buymovies", "seasons", "rentalmovies"), default="buymovies",
                   help="Item type for batch mode (default: buymovies). rentalmovies is "
                        "recognized only to report that the public API lacks rental prices.")
    p.add_argument("--store", default=DEFAULT_STORE, help=f"Store (default: {DEFAULT_STORE})")
    p.add_argument("--country", default=DEFAULT_COUNTRY, help=f"Country code (default: {DEFAULT_COUNTRY})")
    p.add_argument("--limit", type=positive_int, default=DEFAULT_LIMIT,
                   help=f"Deals pool size for batch mode (default: {DEFAULT_LIMIT})")
    p.add_argument("--min-savings", type=positive_float, default=None,
                   help="Only show deals with at least this savings vs priceBefore, "
                        "in the selected store/country currency.")
    p.add_argument("--since", type=positive_int, default=None, metavar="N",
                   help="Only show deals whose last price change was within the last N days "
                        "(e.g. --since 1 for today's drops). Batch mode only.")
    p.add_argument("--sort", choices=VALID_SORTS, default=DEFAULT_SORT,
                   help=f"Sort order for Deals (default: {DEFAULT_SORT})")
    p.add_argument("--genre", default=None,
                   help="Filter by genre (e.g. Horror, Drama, SciFiFantasy; case-insensitive). "
                        "Supported only with --type buymovies; other types are rejected "
                        "(Pitfall #21). Unknown values are rejected (Pitfall #22).")
    p.add_argument("--max-price", type=positive_float, default=None,
                   help="Filter to deals at or below this amount in the selected "
                        "store/country currency.")
    p.add_argument("--release-year", type=release_year_value, default=None,
                   help="Filter by release year or range (e.g. 2026-2026, 2024-2026).")
    p.add_argument("--quality", choices=VALID_QUALITIES, default="hd4k",
                   help="Server-side quality filter (default: hd4k). sd/sdOnly select the SD "
                        "DetailData tier; hd4k/hd/4k prefer HD and fall back to SD. "
                        "4k is supported only with --type buymovies.")
    p.add_argument("--exclude-bundles", action="store_true",
                   help="Skip multi-film collections (isMovieBundle=1). Useful when you "
                        "want individual movies with ratings - bundles don't carry IMDb/RT scores.")
    p.add_argument("--atl-only", action="store_true",
                   help="Filter to ATL (all-time-low) rows only. v2.x default behavior; "
                        "v3.0 defaults to showing all deals with ATL as a column flag.")
    p.add_argument("--json", action="store_true", help="Emit JSON (batch mode only)")
    args = p.parse_args()

    supplied = supplied_long_options(sys.argv[1:])
    if args.title and args.type == "rentalmovies":
        print("  --type rentalmovies is unavailable: Deals/Charts reject rentalmovies, "
              "and Prices silently returns purchase data instead of rental prices "
              "(Pitfall #38)", file=sys.stderr)
        return 2

    if args.title:
        if "--json" in supplied:
            print("  --json is batch-only and cannot be combined with --title", file=sys.stderr)
            return 2
        ignored = [option for option in TITLE_BATCH_ONLY_OPTIONS if option in supplied]
        if ignored:
            print(f"  warning: --title ignores batch-only option(s): {', '.join(ignored)}",
                  file=sys.stderr)

    if not args.title and args.genre and args.type != "buymovies":
        print(f"  --genre requires --type buymovies; CheapCharts does not reliably apply "
              f"genre to {args.type} results (Pitfall #21)", file=sys.stderr)
        return 2

    # Validate genre before calling the API: unknown values are silently
    # treated as "All" server-side, which would return every deal unfiltered.
    if not args.title and args.genre:
        genre = normalize_genre(args.genre)
        if genre is None:
            print(f"  unknown genre '{args.genre}' (the API silently ignores unknown genres "
                  f"and returns ALL deals)", file=sys.stderr)
            print(f"  valid genres: {', '.join(VALID_GENRES)}", file=sys.stderr)
            return 2
        args.genre = genre

    if not args.title and args.type == "seasons" and args.quality == "4k":
        print("  --quality 4k is unsupported with --type seasons: CheapCharts Deals "
              "rejects this combination. Omit --quality or use hd, sd, or sdOnly "
              "(Pitfall #39)", file=sys.stderr)
        return 2

    if not args.title and args.type == "rentalmovies":
        print("  --type rentalmovies is unavailable: Deals/Charts reject rentalmovies, "
              "and Prices silently returns purchase data instead of rental prices "
              "(Pitfall #38)", file=sys.stderr)
        return 2

    if args.history and not args.title:
        print("  --history requires --title (history is a per-title lookup)", file=sys.stderr)
        return 2

    try:
        if args.title:
            return check_single_title(args.title, args.store, args.country, show_history=args.history)
        if args.store == "games":
            print("  CheapCharts Games has no public API (verified 2026-06-23).", file=sys.stderr)
            print("  For current game deals, see: https://games.cheapcharts.com", file=sys.stderr)
            print("  Or use the CheapCharts Games mobile apps "
                  "(iOS: id1622193150, Android: com.cheapcharts.cheapcharts_games).", file=sys.stderr)
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
            since_days=args.since,
        )
    except Exception as e:
        print(f"  error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
