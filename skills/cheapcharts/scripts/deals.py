#!/usr/bin/env python3
"""
deals.py - CheapCharts deals, title evidence, and purchase decisions

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
    python deals.py --decide "Heat"           # one-title Buy / Wait / Skip receipt
    python deals.py --decide "Heat" --json    # discriminated decision-state envelope
    python deals.py --scoped-json             # additive provenance-rich Browse envelope

Combined filters (per llms.txt guideline #11):
    python deals.py --genre Horror --max-price 4.99 --min-savings 3 --atl-only

Exit codes:
    0 - success (deals returned, factual title checked, or a decision issued)
    1 - legitimate empty or non-decision state (not found, ambiguous, insufficient evidence)
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
from contextlib import suppress
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from decision_engine import (
    DecisionRequest,
    HistoricalComparator,
    Offer,
    PurchaseConstraints,
    evaluate_decision,
)

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
DECISION_PATIENCE = ("low", "balanced", "flexible")
DECISION_FORMATS = ("SD", "HD", "4K")
DECISION_INTENTS = ("new", "new_purchase", "upgrade")


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


def search_candidates(title, store=DEFAULT_STORE, country=DEFAULT_COUNTRY):
    """Return ranked title candidates plus an API error, if any."""
    url = (
        f"{API_BASE}/gptapi/Search.php?action=search&store={store}"
        f"&country={country}&itemType=all&query={quote(title)}&limit=5"
    )
    data = fetch(url)
    if data.get("status") == "error":
        return [], data.get("message", "unknown Search error")
    ranked = sorted(
        data.get("results", []),
        key=lambda result: score_match(title, result.get("title")),
        reverse=True,
    )
    candidates = []
    for result in ranked:
        itype, sid = get_id_from_url(result.get("cheapChartsProductPageUrl", ""))
        if not (itype and sid):
            continue
        candidates.append({
            "title": result.get("title"),
            "item_type": itype,
            "id_in_store": sid,
            "score": round(score_match(title, result.get("title")), 4),
            "cheapcharts_url": result.get("cheapChartsProductPageUrl"),
            "release_year": result.get("releaseYear"),
        })
    return candidates, None


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
    candidates, error = search_candidates(title, store, country)
    if error:
        return None, None, error
    if not candidates:
        return None, None, None
    best = candidates[0]
    if best["score"] < 0.6 and len(candidates) > 1:
        others = "; ".join(candidate.get("title") or "?" for candidate in candidates[1:])[:200]
        print(f"  warning: weak title match for '{title}' - using '{best.get('title')}'",
              file=sys.stderr)
        print(f"  other search hits: {others}", file=sys.stderr)
    return best["item_type"], best["id_in_store"], None


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


def non_negative_float(value):
    """Argparse type for finite numeric options whose valid domain includes zero."""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
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


def scope_value(value, provenance):
    """Represent one effective-scope dimension with explicit provenance."""
    return {"value": value, "provenance": provenance}


def decision_applied_scope(title, store, country, budget, patience, required_format,
                           intent, supplied, selected_tier=None):
    """Build the self-contained effective scope shared by all decision states."""
    return {
        "lane": scope_value("decide", "user_set"),
        "title_query": scope_value(title, "user_set"),
        "store": scope_value(store, "user_set" if "--store" in supplied else "default"),
        "country": scope_value(country, "user_set" if "--country" in supplied else "default"),
        "budget_ceiling": scope_value(
            budget, "user_set" if "--budget" in supplied else "default"
        ),
        "patience": scope_value(
            patience or "balanced", "user_set" if "--patience" in supplied else "default"
        ),
        "required_format": scope_value(
            required_format or "any",
            "user_set" if "--required-format" in supplied else "default",
        ),
        "intent": scope_value(
            intent or "unspecified", "user_set" if "--intent" in supplied else "default"
        ),
        "selected_tier": scope_value(selected_tier, "derived"),
    }


def browse_applied_scope(item_type, store, country, limit, sort, genre, max_price,
                         release_year, quality, exclude_bundles, atl_only, since_days,
                         min_savings, supplied):
    """Build a complete Browse scope without changing the legacy raw JSON rows."""
    def source(option):
        return "user_set" if option in supplied else "default"

    return {
        "lane": scope_value("browse", "user_set"),
        "item_type": scope_value(item_type, source("--type")),
        "store": scope_value(store, source("--store")),
        "country": scope_value(country, source("--country")),
        "limit": scope_value(limit, source("--limit")),
        "sort": scope_value(sort, source("--sort")),
        "genre": scope_value(genre or "All", source("--genre")),
        "max_price": scope_value(max_price, source("--max-price")),
        "release_year": scope_value(release_year, source("--release-year")),
        "quality": scope_value(quality, source("--quality")),
        "exclude_bundles": scope_value(exclude_bundles, source("--exclude-bundles")),
        "atl_only": scope_value(atl_only, source("--atl-only")),
        "since_days": scope_value(since_days, source("--since")),
        "minimum_savings": scope_value(min_savings, source("--min-savings")),
    }


def print_json_envelope(envelope):
    print(json.dumps(envelope, indent=2))


def decision_error_envelope(title, store, country, constraints, supplied, message):
    return {
        "state": "error",
        "request": {"mode": "decide", "title": title},
        "applied_scope": decision_applied_scope(
            title, store, country, constraints.budget_ceiling, constraints.patience,
            constraints.required_format, constraints.intent, supplied,
        ),
        "error": {"message": str(message), "retryable": True},
        "next_action": "Retry the same decision request after the data source recovers.",
    }


def resolve_decision_candidate(title, store, country):
    """Resolve a title conservatively for advice, returning a state plus payload."""
    candidates, error = search_candidates(title, store, country)
    if error:
        return "error", {"message": error, "retryable": True}
    if not candidates:
        return "not_found", None

    exact = [candidate for candidate in candidates if _norm_title(candidate["title"]) == _norm_title(title)]
    if len(exact) == 1:
        return "resolved", exact[0]

    best = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None
    ambiguous = best["score"] < 0.72 or (
        runner_up is not None and best["score"] - runner_up["score"] < 0.12
    )
    if ambiguous:
        return "disambiguation", candidates
    return "resolved", best


def decision_price_tier(node):
    """Select the coherent offered tier; format requirements do not change pricing."""
    selected = select_price_tier(node, None)
    api_tier = selected["tier"].title()
    selected["atl_signal"] = node.get(f"price{api_tier}IsLowest")
    selected["evolution"] = node.get(f"price{api_tier}Evolution")
    if selected["tier"] == "sd":
        selected["format"] = "SD"
    elif node.get("has4K") in (1, True):
        selected["format"] = "4K"
    else:
        selected["format"] = "HD"
    return selected


def decision_comparators(selected):
    """Adapt selected-tier history into trustworthy floor/comparable evidence."""
    # Evolution is newest-first and its newest segment is the current price,
    # not historical evidence. Only older observations can establish a prior
    # floor or recurrence cadence.
    entries = parse_evolution(selected.get("evolution"))[1:]
    comparators = []
    if entries:
        floor = min(price for _, _, price in entries)
        comparators.extend(
            HistoricalComparator(price=price, observed_on=observed_on, kind="historical_floor")
            for observed_on, _direction, price in entries
            if abs(price - floor) <= 0.01
        )
    was = selected.get("was")
    if was is not None:
        with suppress(TypeError, ValueError):
            comparators.append(HistoricalComparator(price=float(was), kind="prior_comparable"))
    return tuple(comparators)


def render_decision_human(envelope):
    """Render the layered human form from the same structured decision facts."""
    state = envelope["state"]
    scope = envelope["applied_scope"]
    scope_line = (
        f"Scope: Decide | store={scope['store']['value']} ({scope['store']['provenance']}) | "
        f"country={scope['country']['value']} ({scope['country']['provenance']}) | "
        f"budget={scope['budget_ceiling']['value'] if scope['budget_ceiling']['value'] is not None else 'none'} "
        f"({scope['budget_ceiling']['provenance']}) | patience={scope['patience']['value']} "
        f"({scope['patience']['provenance']}) | required-format={scope['required_format']['value']} "
        f"({scope['required_format']['provenance']}) | intent={scope['intent']['value']} "
        f"({scope['intent']['provenance']})"
    )
    lines = [scope_line]
    if state == "not_found":
        lines.append(f"Not found: {envelope['request']['title']}")
        lines.append(envelope["next_action"])
        return "\n".join(lines)
    if state == "error":
        lines.append(f"Error: {envelope['error']['message']}")
        lines.append(envelope["next_action"])
        return "\n".join(lines)
    if state == "disambiguation":
        lines.append("I need one title confirmation before giving purchase advice:")
        for index, candidate in enumerate(envelope["candidates"], 1):
            year = f" ({candidate['release_year']})" if candidate.get("release_year") else ""
            lines.append(
                f"  {index}. {candidate['title']}{year} "
                f"[{candidate['item_type']}, {candidate['id_in_store']}]"
            )
        lines.append(envelope["next_action"])
        return "\n".join(lines)

    offer = envelope["offer"]
    lines.append(
        f"Title: {offer['title']} | {offer['format_tier']} | "
        f"{format_money(offer['current_price'], offer['currency'], 'g')}"
    )
    if state == "insufficient_evidence":
        lines.append("Insufficient evidence — no Buy / Wait / Skip verdict issued.")
        if envelope.get("missing_requirements"):
            lines.append("Missing: " + "; ".join(envelope["missing_requirements"]))
        if envelope.get("conflicts"):
            lines.append("Conflicts: " + "; ".join(envelope["conflicts"]))
        lines.append(envelope["next_action"])
        return "\n".join(lines)

    lines.append(
        f"{envelope['verdict'].upper()} — {envelope['confidence']} confidence: "
        f"{envelope['decisive_reason']}"
    )
    objective = envelope["objective_deal_strength"]
    lines.append(
        f"Objective deal strength: {objective['label'].title()} "
        f"(transparent component score {objective['component_score']})."
    )
    for evidence in envelope["decisive_evidence"]:
        lines.append(f"  - {evidence}")
    personal = envelope["personal_fit"]
    lines.append(
        f"Personal fit: {personal['assessment'].replace('_', ' ')}; "
        f"{personal['effect'].replace('_', ' ')}."
    )
    coverage = envelope["evidence_coverage"]
    lines.append(
        f"Evidence coverage: {coverage['trustworthy_historical_comparators']} trustworthy comparator(s), "
        f"{coverage['dated_comparators']} dated; missing: "
        f"{', '.join(coverage['missing_signals']) or 'none'}."
    )
    recurrence = envelope.get("recurrence")
    if recurrence:
        if recurrence.get("eligible"):
            lines.append(f"Recurrence: {recurrence['broad_window']}; {recurrence['guidance']}")
        else:
            lines.append(f"Recurrence: {recurrence['guidance']}")
    lines.append("Applied constraints:")
    for name, value in envelope["applied_constraints"].items():
        display = value["value"] if value["value"] is not None else value["default_meaning"]
        lines.append(f"  - {name}: {display} ({value['source']})")
    for caveat in envelope["caveats"]:
        lines.append(f"Caveat: {caveat}")
    if offer.get("store_url"):
        lines.append(f"Buy link: {offer['store_url']}")
    if offer.get("cheapcharts_url"):
        lines.append(f"Price history: {offer['cheapcharts_url']}")
    return "\n".join(lines)


def check_decision(title, store=DEFAULT_STORE, country=DEFAULT_COUNTRY, budget=None,
                   patience=None, required_format=None, intent=None, output_json=False,
                   supplied=None):
    """Resolve one title, evaluate its selected-tier evidence, and emit one state."""
    supplied = supplied or set()
    constraints = PurchaseConstraints(
        budget_ceiling=budget,
        patience=patience,
        required_format=required_format,
        intent=intent,
    )
    resolution_state, payload = resolve_decision_candidate(title, store, country)
    scope = decision_applied_scope(
        title, store, country, budget, patience, required_format, intent, supplied,
    )
    shared = {
        "request": {"mode": "decide", "title": title},
        "applied_scope": scope,
    }
    if resolution_state == "error":
        envelope = {
            "state": "error", **shared, "error": payload,
            "next_action": "Retry the same decision request after the data source recovers.",
        }
        rc = 2
    elif resolution_state == "not_found":
        envelope = {
            "state": "not_found", **shared,
            "next_action": "Check the title spelling or add a release year.",
        }
        rc = 1
    elif resolution_state == "disambiguation":
        envelope = {
            "state": "disambiguation", **shared, "candidates": payload,
            "next_action": "Choose one candidate by title and identity, then retry --decide.",
        }
        rc = 1
    else:
        candidate = payload
        node = fetch_detail(candidate["item_type"], candidate["id_in_store"], store, country)
        if not node or node.get("_error"):
            message = node.get("_error", "empty DetailData response") if node else "empty DetailData response"
            envelope = {
                "state": "error", **shared,
                "title": candidate,
                "error": {"message": message, "retryable": True},
                "next_action": "Retry the same decision request after title detail data recovers.",
            }
            rc = 2
        else:
            selected = decision_price_tier(node)
            scope["selected_tier"] = scope_value(selected["tier"], "derived")
            currency = resolve_currency(node.get("currency"), country)
            actual_title = node.get("title") or candidate["title"]
            request = DecisionRequest(
                offer=Offer(
                    title=actual_title,
                    title_id=candidate["id_in_store"],
                    store=store,
                    country=country,
                    format_tier=selected["format"],
                    current_price=selected["price"],
                    currency=currency,
                    regular_price=selected["was"],
                ),
                history=decision_comparators(selected),
                constraints=constraints,
                authoritative_atl=(
                    selected["atl_signal"] == 1 if selected["atl_signal"] in (0, 1) else None
                ),
            )
            result = evaluate_decision(request).to_dict()
            envelope = {**shared, **result}
            envelope["offer"].update({
                "item_type": candidate["item_type"],
                "selected_tier": selected["tier"],
                "store_url": node.get("productPageUrl") or node.get("iTunesUrl"),
                "cheapcharts_url": node.get("cheapChartsProductPageUrl") or candidate.get("cheapcharts_url"),
            })
            rc = 0 if envelope["state"] == "decision" else 1

    if output_json:
        print_json_envelope(envelope)
    else:
        print(render_decision_human(envelope))
    return rc


def check_batch(item_type, store=DEFAULT_STORE, country=DEFAULT_COUNTRY, limit=DEFAULT_LIMIT,
                min_savings=None, output_json=False, sort=DEFAULT_SORT,
                genre=None, max_price=None, release_year=None, quality=None,
                exclude_bundles=False, atl_only=False, since_days=None,
                output_scoped_json=False, supplied=None):
    """Pull current deals with optional filters, then in parallel verify
    each via DetailData's IsLowest flag."""
    supplied = supplied or set()
    applied_scope = browse_applied_scope(
        item_type, store, country, limit, sort, genre, max_price, release_year,
        quality, exclude_bundles, atl_only, since_days, min_savings, supplied,
    )
    deals_url = build_deals_url(
        item_type, store, country, limit, sort, genre, max_price, release_year, quality
    )
    sent_params = dict(parse_qsl(urlsplit(deals_url).query))
    data = fetch(deals_url)
    if data.get("status") != "success":
        msg = data.get("message", "unknown")
        if output_scoped_json:
            print_json_envelope({
                "state": "error",
                "request": {"mode": "browse"},
                "applied_scope": applied_scope,
                "error": {"message": msg, "retryable": True},
                "next_action": "Retry the same Browse scope after the data source recovers.",
            })
        print(f"  deals fetch failed: {msg}", file=sys.stderr)
        if store != DEFAULT_STORE:
            print(f"  note: CheapCharts' Deals endpoint is most stable for iTunes. For {store},", file=sys.stderr)
            print("  try --title <name> for a single-title lookup, or use --store itunes for batch.", file=sys.stderr)
        return 2
    deals = data.get("results", {}).get(item_type, [])
    if not deals:
        if output_scoped_json:
            print_json_envelope({
                "state": "empty",
                "request": {"mode": "browse"},
                "applied_scope": applied_scope,
                "results": [],
                "next_action": "Relax one filter or choose a wider window without changing this scope silently.",
            })
            print(f"  no {item_type} deals returned", file=sys.stderr)
        elif output_json:
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
        if output_scoped_json:
            print_json_envelope({
                "state": "error",
                "request": {"mode": "browse"},
                "applied_scope": applied_scope,
                "error": {"message": "No Deals item had a usable canonical title identity.", "retryable": False},
                "next_action": "Treat this as response-schema drift; do not use positional rows.",
            })
        elif output_json:
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
        if output_scoped_json:
            print_json_envelope({
                "state": "error",
                "request": {"mode": "browse"},
                "applied_scope": applied_scope,
                "error": {"message": "All DetailData lookups failed.", "retryable": True},
                "next_action": "Retry the same Browse scope after DetailData recovers.",
            })
        print("  all DetailData lookups failed - treating as API error", file=sys.stderr)
        return 2

    if output_scoped_json:
        state = "results" if atl else "empty"
        envelope = {
            "state": state,
            "request": {"mode": "browse"},
            "applied_scope": applied_scope,
            "results": atl,
            "result_metadata": {
                "candidate_count": len(candidates),
                "detail_failures": failed,
                "canonical_identity": "cheapChartsProductPageUrl",
            },
        }
        if state == "empty":
            envelope["next_action"] = (
                "No rows matched the effective scope; relax one filter without changing this scope silently."
            )
        print_json_envelope(envelope)
    elif output_json:
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
        description="CheapCharts deals, factual title evidence, and Buy / Wait / Skip decisions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Browse: python deals.py --genre Horror --max-price 4.99 --min-savings 3\n"
            "Decide: python deals.py --decide \"Heat\" --budget 10 --required-format 4K"
        ),
    )
    title_mode = p.add_mutually_exclusive_group()
    title_mode.add_argument("--title", help="Inspect one title factually (resolves via Search)")
    title_mode.add_argument("--decide", metavar="TITLE",
                            help="Decide whether to Buy / Wait / Skip one resolved title")
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
    p.add_argument("--budget", type=non_negative_float, default=None,
                   help="With --decide: personal budget ceiling in the selected market currency")
    p.add_argument("--patience", choices=DECISION_PATIENCE, default=None,
                   help="With --decide: low, balanced, or flexible waiting preference")
    p.add_argument("--required-format", type=str.upper, choices=DECISION_FORMATS, default=None,
                   help="With --decide: minimum acceptable capability (SD < HD < 4K)")
    p.add_argument("--intent", choices=DECISION_INTENTS, default=None,
                   help="With --decide: new/new_purchase or upgrade intent")
    p.add_argument("--json", action="store_true",
                   help="Emit raw JSON rows in Browse, or the decision envelope with --decide")
    p.add_argument("--scoped-json", action="store_true",
                   help="Emit the additive provenance-rich Browse envelope; raw --json remains compatible")
    args = p.parse_args()

    supplied = supplied_long_options(sys.argv[1:])
    if args.intent == "new":
        args.intent = "new_purchase"

    decision_only = {"--budget", "--patience", "--required-format", "--intent"}
    used_decision_only = sorted(decision_only & supplied)
    if used_decision_only and not args.decide:
        print(f"  {', '.join(used_decision_only)} require --decide TITLE", file=sys.stderr)
        return 2

    if args.json and args.scoped_json:
        print("  --json and --scoped-json are alternative structured-output contracts", file=sys.stderr)
        return 2

    if args.decide and args.scoped_json:
        print("  --scoped-json is Browse-only; use --decide TITLE --json for a decision envelope",
              file=sys.stderr)
        return 2

    if args.decide and args.type == "rentalmovies":
        print("  --type rentalmovies is unavailable: Deals/Charts reject rentalmovies, "
              "and Prices silently returns purchase data instead of rental prices "
              "(Pitfall #38)", file=sys.stderr)
        return 2

    if args.decide:
        incompatible = sorted(
            set(TITLE_BATCH_ONLY_OPTIONS + ("--history",)) & supplied
        )
        if incompatible:
            print(f"  --decide cannot be combined with Browse/Inspect option(s): {', '.join(incompatible)}",
                  file=sys.stderr)
            return 2

    if args.title and args.type == "rentalmovies":
        print("  --type rentalmovies is unavailable: Deals/Charts reject rentalmovies, "
              "and Prices silently returns purchase data instead of rental prices "
              "(Pitfall #38)", file=sys.stderr)
        return 2

    if args.title:
        if "--json" in supplied:
            print("  --json is batch-only and cannot be combined with --title", file=sys.stderr)
            return 2
        if "--scoped-json" in supplied:
            print("  --scoped-json is Browse-only and cannot be combined with --title", file=sys.stderr)
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

    if args.store == "games":
        message = "CheapCharts Games has no public API (verified 2026-06-23)."
        if args.decide and args.json:
            print_json_envelope({
                "state": "unsupported",
                "request": {"mode": "decide", "title": args.decide},
                "applied_scope": decision_applied_scope(
                    args.decide, args.store, args.country, args.budget, args.patience,
                    args.required_format, args.intent, supplied,
                ),
                "unsupported": {
                    "capability": "store",
                    "value": "games",
                    "message": message,
                    "retryable": False,
                },
                "next_action": (
                    "Choose a supported digital-video store, or visit "
                    "https://games.cheapcharts.com for game prices."
                ),
            })
        else:
            print(f"  {message}", file=sys.stderr)
            print("  For current game deals, see: https://games.cheapcharts.com", file=sys.stderr)
            print("  Or use the CheapCharts Games mobile apps "
                  "(iOS: id1622193150, Android: com.cheapcharts.cheapcharts_games).", file=sys.stderr)
        return 2

    try:
        if args.decide:
            return check_decision(
                args.decide,
                store=args.store,
                country=args.country,
                budget=args.budget,
                patience=args.patience,
                required_format=args.required_format,
                intent=args.intent,
                output_json=args.json,
                supplied=supplied,
            )
        if args.title:
            return check_single_title(args.title, args.store, args.country, show_history=args.history)
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
            output_scoped_json=args.scoped_json,
            supplied=supplied,
        )
    except Exception as e:
        if args.decide and args.json:
            constraints = PurchaseConstraints(
                budget_ceiling=args.budget,
                patience=args.patience,
                required_format=args.required_format,
                intent=args.intent,
            )
            print_json_envelope(decision_error_envelope(
                args.decide, args.store, args.country, constraints, supplied, e,
            ))
        elif args.scoped_json:
            print_json_envelope({
                "state": "error",
                "request": {"mode": "browse"},
                "applied_scope": browse_applied_scope(
                    args.type, args.store, args.country, args.limit, args.sort, args.genre,
                    args.max_price, args.release_year, args.quality, args.exclude_bundles,
                    args.atl_only, args.since, args.min_savings, supplied,
                ),
                "error": {"message": str(e), "retryable": True},
                "next_action": "Retry the same Browse scope after the data source recovers.",
            })
        print(f"  error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
