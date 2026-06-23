#!/usr/bin/env python3
"""
atl_check.py - CheapCharts All-Time-Low (ATL) checker

Verifies whether current CheapCharts deals are at their all-time low
using the authoritative priceHdIsLowest / priceSdIsLowest flags from
DetailData. Use the SKILL.md ATL recipe for full context.

Usage:
    python atl_check.py                       # batch: all current deals at ATL
    python atl_check.py --title "Fight Club"  # single title lookup
    python atl_check.py --type seasons        # check TV seasons instead of movies
    python atl_check.py --limit 30            # narrower deal pool
    python atl_check.py --min-savings 5       # only show items with $5+ savings
    python atl_check.py --json                # machine-readable output

Exit codes:
    0 - success (at least one ATL item or single-title check completed)
    1 - no ATL items found / single title not found
    2 - API error
"""

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

API_BASE = "https://buster.cheapcharts.de/v1"
DEFAULT_STORE = "itunes"
DEFAULT_COUNTRY = "us"
DEFAULT_LIMIT = 80
HTTP_TIMEOUT = 20
MAX_WORKERS = 8


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
    """Hit DetailData for a single title. Returns the inner node dict."""
    url = f"{API_BASE}/DetailData.php?store={store}&country={country}&itemType={itype}&idInStore={sid}"
    data = fetch(url)
    # CRITICAL: DetailData does NOT use the 'status' field. It uses results.<itemType>.
    return data.get("results", {}).get(itype, {})


def search_id(title, store=DEFAULT_STORE, country=DEFAULT_COUNTRY):
    """Search for a title, return first (itype, sid) or (None, None)."""
    from urllib.parse import quote
    url = f"{API_BASE}/gptapi/Search.php?action=search&store={store}&country={country}&itemType=all&query={quote(title)}&limit=1"
    data = fetch(url)
    results = data.get("results", [])
    if not results:
        return None, None
    return get_id_from_url(results[0].get("cheapChartsProductPageUrl", ""))


def is_atl(node):
    """True if HD or SD current price is at the all-time low (authoritative flag)."""
    return node.get("priceHdIsLowest") == 1 or node.get("priceSdIsLowest") == 1


def format_atl_line(a):
    """Pretty-print one ATL item."""
    price = a.get("price")
    was = a.get("was")
    if price is not None and was is not None and was != price:
        try:
            save = float(was) - float(price)
            save_str = f" (was ${was}, save ${save:.2f})"
        except (TypeError, ValueError):
            save_str = f" (was ${was})"
    else:
        save_str = ""
    atl_label = "HD" if a.get("is_atl_hd") and not a.get("is_atl_sd") else (
        "SD" if a.get("is_atl_sd") and not a.get("is_atl_hd") else "BOTH"
    )
    return f"  [{atl_label}] {a['title']} | ${price}{save_str} | changed {a.get('change_date', '?')} | {a['url']}"


def check_single_title(title, store=DEFAULT_STORE, country=DEFAULT_COUNTRY):
    """Resolve a title via Search, then check DetailData for ATL status."""
    itype, sid = search_id(title, store, country)
    if not sid:
        print(f"  not found: '{title}'")
        return 1
    node = fetch_detail(itype, sid, store, country)
    if not node:
        print(f"  detail lookup failed for '{title}'")
        return 2
    price = node.get("priceHd") or node.get("priceSd")
    print(f"  {node.get('title')}: ${price}")
    print(f"    ATL (IsLowest):      hd={node.get('priceHdIsLowest')} sd={node.get('priceSdIsLowest')}")
    print(f"    Current-sale floor (IsBest): hd={node.get('priceHdIsBest')} sd={node.get('priceSdIsBest')}")
    print(f"    Last change: {node.get('priceHdLastChangeDate') or node.get('priceSdLastChangeDate')}")
    if is_atl(node):
        print("    --> Currently at ATL")
    return 0


def check_batch(item_type, store=DEFAULT_STORE, country=DEFAULT_COUNTRY, limit=DEFAULT_LIMIT,
                min_savings=None, output_json=False):
    """Pull current deals, then in parallel verify each via DetailData's IsLowest flag."""
    deals_url = (
        f"{API_BASE}/gptapi/Deals.php?action=getDeals&store={store}&country={country}"
        f"&itemType={item_type}&sort=greatestSavings&limit={limit}"
    )
    data = fetch(deals_url)
    if data.get("status") != "success":
        print(f"  deals fetch failed: {data.get('message', 'unknown')}", file=sys.stderr)
        return 2
    deals = data.get("results", {}).get(item_type, [])
    if not deals:
        print(f"  no {item_type} deals returned")
        return 1

    # Map each deal to (deal, itype, sid) up front
    candidates = []
    for d in deals:
        itype, sid = get_id_from_url(d.get("cheapChartsProductPageUrl", ""))
        if itype and sid:
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
            if not is_atl(node):
                continue
            price = node.get("priceHd") or node.get("priceSd")
            was = node.get("priceHdBefore") or node.get("priceSdBefore")
            if min_savings is not None and price is not None and was is not None:
                try:
                    if float(was) - float(price) < min_savings:
                        continue
                except (TypeError, ValueError):
                    pass
            atl.append({
                "title": node.get("title"),
                "price": price,
                "was": was,
                "change_date": node.get("priceHdLastChangeDate") or node.get("priceSdLastChangeDate"),
                "is_atl_hd": node.get("priceHdIsLowest") == 1,
                "is_atl_sd": node.get("priceSdIsLowest") == 1,
                "url": d.get("cheapChartsProductPageUrl"),
            })

    if output_json:
        print(json.dumps(atl, indent=2))
    else:
        print(f"=== {len(atl)} {item_type} currently at ATL (out of {len(candidates)} checked) ===\n")
        for a in atl:
            print(format_atl_line(a))
    return 0 if atl else 1


def main():
    p = argparse.ArgumentParser(description="CheapCharts All-Time-Low (ATL) checker")
    p.add_argument("--title", help="Check a single title (resolves via Search)")
    p.add_argument("--type", choices=("buymovies", "seasons"), default="buymovies",
                   help="Item type for batch mode (default: buymovies)")
    p.add_argument("--store", default=DEFAULT_STORE, help=f"Store (default: {DEFAULT_STORE})")
    p.add_argument("--country", default=DEFAULT_COUNTRY, help=f"Country code (default: {DEFAULT_COUNTRY})")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"Deals pool size for batch mode (default: {DEFAULT_LIMIT})")
    p.add_argument("--min-savings", type=float, default=None,
                   help="Only show ATL items with at least this $ savings vs priceBefore")
    p.add_argument("--json", action="store_true", help="Emit JSON (batch mode only)")
    args = p.parse_args()

    try:
        if args.title:
            return check_single_title(args.title, args.store, args.country)
        return check_batch(
            item_type=args.type,
            store=args.store,
            country=args.country,
            limit=args.limit,
            min_savings=args.min_savings,
            output_json=args.json,
        )
    except Exception as e:
        print(f"  error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
