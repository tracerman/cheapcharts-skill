#!/usr/bin/env python3
"""Live canary for the empirically-discovered API behaviors in SKILL.md.

The skill's pitfall knowledge base is a set of dated empirical claims about
the CheapCharts API. This script re-tests the load-bearing ones so drift is
caught by the weekly schedule instead of by a confused end user.

Exit codes:
    0 - all assumptions still hold
    1 - DRIFT: the API changed behavior; the matching pitfall needs a re-check
    2 - ERROR: the API is unreachable/broken; try again later
"""

import json
import sys
import urllib.request

BASE = "https://buster.cheapcharts.de/v1"
GPT = f"{BASE}/gptapi"
TIMEOUT = 30


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def check_deals_alive():
    """Baseline: the Deals endpoint answers with status=success."""
    data = fetch(f"{GPT}/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&limit=5")
    if data.get("status") != "success":
        return "error", f"Deals returned status={data.get('status')}: {data.get('message')}"
    if not data.get("results", {}).get("buymovies"):
        return "error", "Deals returned success but no buymovies items"
    return "ok", "Deals endpoint healthy"


def check_detaildata_vocabulary():
    """Pitfall #13: DetailData wants itemType=movies (buymovies must fail)."""
    deals = fetch(f"{GPT}/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&limit=5")
    items = deals.get("results", {}).get("buymovies", [])
    sid = None
    for item in items:
        url = item.get("cheapChartsProductPageUrl", "")
        if "/movies/" in url:
            sid = url.rsplit("/", 1)[-1].split("?")[0]
            break
    if not sid:
        return "error", "could not extract an idInStore from Deals to probe DetailData"
    good = fetch(f"{BASE}/DetailData.php?store=itunes&country=us&itemType=movies&idInStore={sid}")
    if not good.get("results", {}).get("movies"):
        return "drift", f"DetailData itemType=movies no longer returns a movies node (id {sid})"
    bad = fetch(f"{BASE}/DetailData.php?store=itunes&country=us&itemType=buymovies&idInStore={sid}")
    if bad.get("results", {}).get("buymovies"):
        return "drift", "DetailData now ACCEPTS itemType=buymovies - Pitfall #13 is stale"
    node = good["results"]["movies"]
    if "priceHdIsLowest" not in node and "priceSdIsLowest" not in node:
        return "drift", "DetailData no longer exposes the IsLowest ATL flags - the whole ATL flow breaks"
    return "ok", "DetailData vocabulary and ATL flags unchanged"


def check_has4k_filter_still_ignored():
    """Pitfall #16: has4K=1 on Deals is silently ignored."""
    data = fetch(f"{GPT}/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&has4K=1&limit=30")
    items = data.get("results", {}).get("buymovies", [])
    if not items:
        return "error", "no items returned for the has4K probe"
    non_4k = [i for i in items if i.get("has4K") not in (1, True)]
    if not non_4k:
        return "drift", ("every item returned with has4K=1 - "
                         "the server-side filter may work now (Pitfall #16 stale)")
    return "ok", f"has4K=1 still ignored ({len(non_4k)}/{len(items)} non-4K items in response)"


def check_recommendations_rating_filter_still_ignored():
    """Pitfall #23: imdbRating is silently ignored on Recommendations."""
    data = fetch(f"{GPT}/Recommendations.php?action=getRecommendations&store=itunes&country=us"
                 f"&itemType=buymovies&genre=Drama&imdbRating=8&limit=20")
    items = data.get("results", {}).get("buymovies", [])
    if not items:
        return "error", "no items returned for the Recommendations probe"
    rated = [i for i in items if i.get("imdbRating") is not None]
    below = [i for i in rated if float(i["imdbRating"]) < 8]
    if rated and not below:
        return "drift", ("Recommendations returned only IMDb>=8 items - "
                         "the rating filter may work now (Pitfall #23 stale)")
    return "ok", f"imdbRating still ignored on Recommendations ({len(below)}/{len(rated)} items below 8)"


def check_no_batch_detaildata():
    """Pitfall #28: DetailData accepts a single idInStore only."""
    deals = fetch(f"{GPT}/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&limit=10")
    ids = []
    for item in deals.get("results", {}).get("buymovies", []):
        url = item.get("cheapChartsProductPageUrl", "")
        if "/movies/" in url:
            ids.append(url.rsplit("/", 1)[-1].split("?")[0])
        if len(ids) == 2:
            break
    if len(ids) < 2:
        return "error", "could not collect two ids for the batch probe"
    data = fetch(f"{BASE}/DetailData.php?store=itunes&country=us&itemType=movies&idInStore={ids[0]},{ids[1]}")
    results = data.get("results") or {}
    if results.get("movies"):
        return "drift", ("DetailData returned data for a comma-separated id list - "
                         "batch may work now (Pitfall #28 stale)")
    return "ok", "still no batch DetailData"


def check_seasons_genre_still_broken():
    """Pitfall #21: genre is silently ignored for itemType=seasons."""
    horror = fetch(f"{GPT}/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&genre=Horror&limit=15")
    drama = fetch(f"{GPT}/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&genre=Drama&limit=15")
    h = [i.get("title") for i in horror.get("results", {}).get("seasons", [])]
    d = [i.get("title") for i in drama.get("results", {}).get("seasons", [])]
    if not h or not d:
        return "error", "no season items returned for the genre probe"
    if h != d:
        return "drift", "Horror and Drama season lists differ - the seasons genre filter may work now (Pitfall #21 stale)"
    return "ok", "seasons genre filter still ignored (identical lists)"


CHECKS = [
    check_deals_alive,
    check_detaildata_vocabulary,
    check_has4k_filter_still_ignored,
    check_recommendations_rating_filter_still_ignored,
    check_no_batch_detaildata,
    check_seasons_genre_still_broken,
]


def main():
    worst = 0
    for check in CHECKS:
        try:
            status, detail = check()
        except Exception as e:  # network failure, JSON error, etc.
            status, detail = "error", f"{type(e).__name__}: {e}"
        icon = {"ok": "PASS ", "drift": "DRIFT", "error": "ERROR"}[status]
        print(f"[{icon}] {check.__name__}: {detail}")
        worst = max(worst, {"ok": 0, "drift": 1, "error": 2}[status])
    return worst


if __name__ == "__main__":
    sys.exit(main())
