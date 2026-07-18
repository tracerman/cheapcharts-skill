# CheapCharts API Recipes

> **This file contains the literal `curl` recipes for the CheapCharts API.** It is separated from `SKILL.md` so the SKILL.md frontmatter and body are free of inline URL+command patterns (which trip content scanners).
>
> **Source of truth:** CheapCharts' official AI agent documentation at https://www.cheapcharts.com/llms.txt (verified 2026-06-23). When llms.txt and these recipes disagree, llms.txt wins.
>
> **For agent use:** when SKILL.md says "see Recipe: <name>", jump to the matching section here. All recipes assume the following URL convention:
>
> ```bash
> CC="https://buster.cheapcharts.de/v1"
> CC_API="$CC/gptapi"
> ```
>
> Set those two shell variables once per session, then run the recipes. They work without them too - just inline the full URL.
>
> **Prefer the bundled script for anything ATL-related:** `python scripts/deals.py` runs the DetailData enrichment in parallel (8 workers) and is 10-12x faster than the sequential inline recipes.

---

## Agent interaction recipes (Browse / Inspect / Decide)

These are host-agent contract examples, not additional API endpoints. The agent should invoke the appropriate script/API workflow immediately and derive both human and structured output from one canonical applied scope.

| Conversation | Route and required behavior |
|---|---|
| `/cheapcharts` | Bare invocation: show one natural orientation line, once per conversation. Do not run a default feed or show a numbered wizard. |
| “Today's Apple TV movie deals under $10” | Browse directly. Scope: Deals source, iTunes/US, movies, today, price <= 10 in response currency; all request dimensions are `user_set`. |
| “Has *Heat* ever been cheaper?” | Inspect. Resolve the title, use DetailData history/ATL evidence, and answer factually without Buy / Wait / Skip. |
| “Should I buy *Heat* now?” | Decide. Resolve one offer, apply supplied constraints plus visible neutral defaults, and require current tier price plus a trustworthy historical comparator before a verdict. |
| “Only 4K” after Browse | Refine the saved Browse frame. Change quality only; label inherited window, subtype, market, and price cap. |
| “What about TV?” after movie-only 4K/genre Browse | Pivot. Clear sort. Name seasons+4K and TV genre casualties; show `degraded-results` only if meaningful supported scope survives, otherwise ask one bounded question before fetching. |
| “Should I buy #3?” | Resolve row 3 against the rendered snapshot. If a newer snapshot exists, echo the likely title and confirm before deciding. |
| “Back to the deals” | Pop the one-title branch, restore saved Browse criteria, refresh live prices, and fully re-echo scope. |
| “Start over” | Clear active frames and return to the one-line orientation with visible market defaults. |
| “What does *Heat* cost to rent?” | Return `unsupported`; public endpoints do not provide verified rental prices, and purchase data is not a substitute. |

When explicit today is empty, say that nothing was recorded as changed today and note source lag. A single optional `--since 3` run is a structurally separate **Nearby: last 3 days** supplement with its own applied scope; it does not replace or mutate the active today frame. For vague “latest,” one labeled widening is allowed. Exact title/date questions never widen silently.

Human scope receipts should be compact. Structured receipts record each dimension's value and provenance (`user_set`, `inherited`, `default`, or `dropped_unsupported`) and preserve fallback, substitution, and retry details. Existing raw batch `--json` stays a list/`[]`; do not assume it has been replaced by the scoped envelope.

Approved CLI surfaces keep factual and advisory title work separate:

```bash
python scripts/deals.py --title "Heat"               # factual title/ATL lookup
python scripts/deals.py --title "Heat" --history     # factual full timeline
python scripts/deals.py --decide "Heat"              # human Buy / Wait / Skip receipt
python scripts/deals.py --decide "Heat" --json       # decision/disambiguation/non-decision envelope
python scripts/deals.py --scoped-json                 # additive scoped Browse envelope
```

Decision constraints are `--budget`, `--patience`, `--required-format`, and `--intent`. Omitted constraints use visible neutral defaults. Raw batch `--json` remains the compatibility list/`[]` mode.

---

## One call per endpoint (quick smoke tests)

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=Fight%20Club&limit=5"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Charts.php?action=getCharts&store=itunes&country=us&itemType=buymovies&genre=All&quality=hd4k&limit=10"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=ActionAdventure&sort=greatestSavings&maxPrice=4.99&limit=20"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Prices.php?action=getPrices&store=itunes&country=us&itemType=buymovies&imdbIDs=tt0468569,tt2911666"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Recommendations.php?action=getRecommendations&store=amazon&country=us&itemType=buymovies&genre=SciFiFantasy&quality=hd4k&limit=15&imdbRating=7"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/DetailData.php?store=itunes&country=us&itemType=seasons&idInStore=1606238021"
```

---

## "Find deals on 4K action movies under $5"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=ActionAdventure&quality=4k&sort=greatestSavings&maxPrice=4.99&limit=20"
```

---

## "Highly-rated deals under $X" (IMDb + maxPrice both filter server-side)

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&imdbRating=7&rottenTomatoesRating=80&maxPrice=9.99&sort=greatestSavings&limit=20"
```

---

## "4K Dolby Vision + Atmos movies on sale" (4K server-side; Vision/Atmos client-side)

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&quality=4k&sort=greatestSavings&limit=50" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['buymovies']
premium = [i for i in items if i.get('has4K') in (1, True) and i.get('hasAtmos') in (1, True) and i.get('hdrFormat') == 'Dolby Vision']
for i in premium[:15]:
    was = i.get('priceBefore','?')
    sav = f\"\${float(was)-float(i['price']):.2f}\" if was != '?' else '?'
    print(f\"{i['title']} | \${i['price']} (was \${was}, save {sav}) | {i['genre']} | IMDb {i.get('imdbRating','?')}\")"
```

---

## "Search a specific title, then compare prices across all 4 stores"

```bash
# Step 1 - find IMDb ID
imdb=$(curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=The%20Dark%20Knight&limit=1" \
  | python -c "import sys,json; print(json.load(sys.stdin)['results'][0].get('imdbId',''))")
echo "IMDb: $imdb"

# Step 2 - query each store in parallel (only iTunes reliably supports prices; others via Topseller)
for store in itunes amazon vudu googlePlay; do
  echo "=== $store ==="
  curl -s "https://buster.cheapcharts.de/v1/gptapi/Prices.php?action=getPrices&store=$store&country=us&itemType=buymovies&imdbIDs=$imdb" \
    | python -c "
import sys, json
r = json.load(sys.stdin)
if r.get('status') != 'success':
    print(f\"  not available: {r.get('message','unknown error')}\")
else:
    for i in r.get('results',{}).get('buymovies',[]):
        print(f\"  \${i.get('price')} (was \${i.get('priceBefore','?')})\")"
done
```

---

## "Complete TV series on sale" (filter to bundle deals, avoid per-season noise)

Do not add `genre`: CheapCharts ignores genre filters for seasons (Pitfall #21).

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&sort=greatestSavings&limit=50" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['seasons']
bundles = [i for i in items if i.get('isBundle') == 1]
for i in bundles[:10]:
    was = i.get('priceBefore','?')
    sav = f\"\${float(was)-float(i['price']):.2f}\" if was != '?' else '?'
    print(f\"{i['title']} | \${i['price']} was \${was} save {sav} | {i['genre']}\")"
```

---

## "Cross-store top sellers today"

Label this output **CheapCharts cross-store top sellers**, not “deals” or “price drops.” A `priceBefore` field can support a factual drop annotation for that item, but it does not turn the Topseller result set into a Deals feed.

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5" | python -c "
import sys, json
r = json.load(sys.stdin)
if r.get('status') != 'success': sys.exit(r.get('message'))
for store, cats in r['results'].items():
    for cat, items in cats.items():
        print(f'=== {store} - {cat} ===')
        for i in items[:5]:
            was = i.get('priceBefore')
            drop = f\" (was \${was})\" if was else ''
            print(f\"  {i['title']} | \${i['price']}{drop} | {i.get('genre','?')}\")"
```

---

## "What's the price of [specific movie]?"

```bash
# Step 1 - Search to get IMDb ID
curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=The%20Dark%20Knight&limit=3"
```

```bash
# Step 2 - Prices using IMDb ID from search results
curl -s "https://buster.cheapcharts.de/v1/gptapi/Prices.php?action=getPrices&store=itunes&country=us&itemType=buymovies&imdbIDs=tt0468569"
```

---

## "Today's price drops on Apple TV/iTunes" (DEFAULT for "latest deals")

Prefer the script - it does all of this in parallel with a `--since` filter:

```bash
python scripts/deals.py --since 1            # movies whose price changed today
python scripts/deals.py --since 3 --type seasons  # only a separately labeled wider scope
```

For an explicit today request, do not silently replace an empty `--since 1` result with `--since 3`. Keep today active and, if useful, render the three-day call as one separate Nearby supplement.

Inline equivalent (sequential, slower):

```bash
# Step 1 - pull the freshest drops (movies + seasons)
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&sort=latestPricechange&limit=80"
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&sort=latestPricechange&limit=80"

# Step 2 - extract idInStore from cheapChartsProductPageUrl, then verify change date
# CRITICAL: DetailData itemType is "movies" or "seasons", NOT "buymovies" (Pitfall #13)
curl -s "https://buster.cheapcharts.de/v1/DetailData.php?store=itunes&country=us&itemType=movies&idInStore=1815368549" | python -c "
import sys, json
m = json.load(sys.stdin)['results']['movies']
print(f\"changeDate: {m.get('priceHdLastChangeDate') or m.get('priceSdLastChangeDate')}\")
print(f\"price: \${m.get('priceHd') or m.get('priceSd')}\")
print(f\"was: \${m.get('priceHdBefore') or m.get('priceSdBefore')}\")
print(f\"evolution: {m.get('priceHdEvolution','')[:120]}\")"
```

---

## "All-time low (ATL) deals"

```bash
# Batch with ATL flag column - all current deals, ATL checkmarks (v3.0 default)
python scripts/deals.py --type buymovies --limit 60

# ATL rows only (v2.x behavior)
python scripts/deals.py --type buymovies --limit 60 --atl-only --min-savings 5

# Check TV seasons instead of movies
python scripts/deals.py --type seasons --limit 30

# Rental prices are unavailable through the public API (Pitfall #38).

# Single title lookup
python scripts/deals.py --title "Fight Club"

# JSON output for piping into other tools
python scripts/deals.py --json --limit 30
```

---

## "Latest new-release movies on sale"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&sort=releaseDate&limit=500" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['buymovies']
real = [i for i in items if not i.get('releaseDate','').startswith('2030')]
real.sort(key=lambda x: x.get('releaseDate',''), reverse=True)
for i in real[:10]:
    print(f\"{i['title']} | {i['releaseDate']} | \${i['price']} (was \${i.get('priceBefore','?')})\")"
```

---

## "Movies released this year that are on sale"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&releaseYear=2026-2026&sort=latestPricechange&limit=20"
```

---

## "TV season releases from a specific year range"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&releaseYear=2024-2026&sort=releaseDate&limit=50"
```

---

## "Charts for new releases only"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Charts.php?action=getCharts&store=itunes&country=us&itemType=buymovies&genre=All&quality=hd4k&releaseYear=2026-2026&limit=20"
```

---

## "Best sci-fi recommendations on Amazon with IMDb 7+"

Label these as **CheapCharts recommendations**, never as deals. The API ignores recommendation rating filters in practice (Pitfall #23); if IMDb 7+ is essential, use and label a Deals query instead.

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Recommendations.php?action=getRecommendations&store=amazon&country=us&itemType=buymovies&genre=SciFiFantasy&quality=hd4k&limit=15&imdbRating=7"
```

Note: Recommendations silently ignores `imdbRating` (Pitfall #23) - for a hard rating floor use Deals with `imdbRating=7` instead.

---

## "What's trending across all stores?"

Label Topseller output **CheapCharts cross-store top sellers**. It is a popularity source, not a deal feed.

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5"
```

---

## "Highly-rated horror movies on sale"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=Horror&sort=greatestSavings&imdbRating=7&limit=20"
```

---

## Movies Anywhere studio lookup

CheapCharts gives you `imdbId`; MA compatibility is a studio-membership question (see [references/EXTRAS.md](references/EXTRAS.md#movies-anywhere-compatibility)):

```bash
curl -s "https://www.imdb.com/title/tt0468569/" -A "Mozilla/5.0" \
  | grep -oE '(Warner Bros|Universal|Sony|Disney|Paramount|MGM|Lionsgate)[^<]*' \
  | head -1
```

---

## Rental prices are unavailable through the public API

Do not use `itemType=rentalmovies` with Deals, Charts, or Prices. Deals and Charts reject it; Prices silently returns purchase data under `results.buymovies`. Search's `priceFollowUpItemType=rentalmovies` is not a working rental-price follow-up. The bundled script recognizes `--type rentalmovies` only to return a clear exit-2 capability error. See [Pitfall #38](references/PITFALLS.md#38-rentalmovies-is-not-a-supported-public-api-price-mode).

---

## Topseller cross-store batch (the only multi-store endpoint)

```bash
# Top 5 sellers per store per category, all four US stores in one call
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5"
```

```bash
# Top 10 across just iTunes and Amazon
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon&maxItemCount=10"
```

```bash
# Topseller for Germany (only iTunes + Amazon supported there)
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=de&store=itunes,amazon&maxItemCount=5"
```

Note: Topseller does NOT take `itemType` - it always returns both `movies` and `seasons` per store. The response is grouped by store, then by movies/seasons.

---

## DetailData - the unofficial internal endpoint (for ATL checks and full price history)

```bash
# Single title detail (iTunes id from Search results)
curl -s "https://buster.cheapcharts.de/v1/DetailData.php?store=itunes&country=us&itemType=movies&idInStore=1815368549"
```

```bash
# Season/bundle detail
curl -s "https://buster.cheapcharts.de/v1/DetailData.php?store=itunes&country=us&itemType=seasons&idInStore=1606238021"
```

The DetailData endpoint is NOT in the official gptapi surface (it was discovered by inspecting CheapCharts' website network calls). It returns the ATL flag (`priceHdIsLowest` / `priceSdIsLowest`), full price history, child seasons for bundles, and other fields the public gptapi endpoints don't expose. For "at ATL right now" checks use the `IsLowest` flag; for history timelines parse the `priceHdEvolution` / `priceSdEvolution` strings with absolute-price semantics (Pitfall #26) - or just use the script recipe below.

---

## "When was [title] on sale?" / price history timeline

```bash
python scripts/deals.py --title "Tom & Jerry Kids Complete Series" --history
```

Output includes every tracked price change as `date -> date: dropped to $X` rows with the historical floor marked. To predict the next sale, look at the cadence of past windows (Black Friday and holiday windows recur most reliably) plus the Seasonal Sales Calendar in [references/EXTRAS.md](references/EXTRAS.md#seasonal-sales-calendar-itunes--apple-tv).

---

## Combined filters (per llms.txt guideline #11)

llms.txt says: "Combine filters for precise results: e.g., Horror movies under $5 with IMDb rating above 7 released between 2015-2025."

```bash
# Horror movies under $5, IMDb 7+, released 2015-2025
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=Horror&maxPrice=4.99&imdbRating=7&releaseYear=2015-2025&sort=greatestSavings&limit=20"
```

```bash
# Or via the script (all filters supported as CLI flags):
python scripts/deals.py --genre Horror --max-price 4.99 --min-savings 3 --release-year 2015-2025 --limit 20
```

---

## Search with pagination (offset parameter)

```bash
# Page 1 (first 20 results)
curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=Batman&limit=20&offset=0"

# Page 2 (results 21-40)
curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=Batman&limit=20&offset=20"
```

---

## Cron / monitoring prompts

For automated deal monitoring, run the script on a schedule and alert on real price drops. Use the "latest price change" workflow (sort=latestPricechange + DetailData verification) rather than `greatestSavings` - the latter is dominated by bundles with manipulated baselines (Pitfall #35).

**Daily "today's drops" report:**

```
Schedule: daily at 9am (0 9 * * *)
Prompt: |
  Run: python scripts/deals.py --since 1 --limit 50
  Report the top 5 deals with title, price, prior price, savings %, IMDb rating,
  ATL flag, and the CheapCharts history link.
  If the table is empty, report the exact today null and source-lag caveat first.
  Optionally run: python scripts/deals.py --since 3 --limit 50
  Render that only as a separate "Nearby: last 3 days" supplement with its own scope;
  never replace the today result or widen again (Pitfall #14).
```

**"Currently at ATL" monitoring** (titles at their all-time low right now, regardless of when they got there):

```
Schedule: daily at 9am (0 9 * * *)
Prompt: |
  Run: python scripts/deals.py --atl-only --min-savings 5
  Report the top 5 ATL titles with title, current price, prior price, savings $ and %,
  IMDb rating, and the CheapCharts history link.
  If no titles meet the threshold, stay silent.
```

**"Just hit ATL today" monitoring** (reached the floor in the last 24 hours):

```
Schedule: daily at 9am (0 9 * * *)
Prompt: |
  Run: python scripts/deals.py --atl-only --since 1 --limit 50
  Report every title with title, current price, prior price, savings $ and %,
  IMDb rating, and the CheapCharts history link.
  If no titles hit ATL today, stay silent.
```
