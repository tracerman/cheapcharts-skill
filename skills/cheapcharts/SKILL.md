---
name: cheapcharts
description: "Browse digital movie and TV deals, inspect one title's price evidence, or decide Buy / Wait / Skip on a current offer across iTunes/Apple TV, Amazon, Vudu, and Google Play via the free CheapCharts public API (no auth or API key). Produces factual deal and history results plus transparent one-title decision receipts. Use for prices, sales, price drops, history, all-time lows, or purchase advice."
version: 3.4.0
license: MIT
metadata:
  author: tracerman
  tags: movies, tv-shows, deals, price-tracking, cheapcharts, atl, all-time-low
  required-commands: python
---

# CheapCharts API Skill

> A free, public-API price tracker for digital movies and TV shows across iTunes (Apple TV), Amazon Prime Video, Vudu, and Google Play. No authentication. Parallel calls are safe - the bundled `deals.py` script uses 8 concurrent DetailData workers.

**Repo:** https://github.com/tracerman/cheapcharts-skill
**API Base URL:** `https://buster.cheapcharts.de/v1/gptapi/`
**ATL check:** `https://buster.cheapcharts.de/v1/DetailData.php` (unofficial internal endpoint - [Pitfall #17](references/PITFALLS.md#17-detaildata-is-unofficial))

## When to Use

- Movie/TV prices, deals, or discounts on digital stores (iTunes/Apple TV, Amazon, Vudu, Google Play)
- Charts (what's popular), recommendations by genre, cross-store price comparison
- Price history / "has it ever been cheaper?" / all-time-low questions
- User mentions CheapCharts directly

**Don't use for:** physical media (Blu-ray/DVD), streaming subscription catalogs (Netflix/Disney+), movie reviews (web search instead), video games (no public API - see [EXTRAS.md](references/EXTRAS.md#cheapcharts-games-related-but-not-in-scope)).

## Adaptive Interaction Contract

CheapCharts is a direct-query skill, not a menu wizard. Route the user's requested outcome into exactly one of three user-visible lanes:

| Lane | User job | Result |
|---|---|---|
| **Browse** | Find a set of current offers within a market, window, or filters | Deal rows or an explicit Browse state |
| **Inspect** | Ask a factual question about one title, offer, ATL status, or history | Factual answer only; never an unsolicited verdict |
| **Decide** | Ask whether to buy one resolved offer now | Buy / Wait / Skip receipt or an explicit non-decision state |

Discovery, capability checks, and conversation/session control are internal classifications, not additional lanes. Execute a specific opening request immediately when its meaning can be preserved. On a bare invocation, show this natural orientation sentence at most once per conversation: “I can show current movie or TV deals, inspect a title's price history, or help decide Buy / Wait / Skip—what are you looking for?” A reply routes directly. Do not ask for optional preferences before returning useful work.

Use meaning, not keywords alone. A set or filtered feed enters Browse. A factual title question such as “Has *Heat* ever been cheaper?” enters Inspect. Purchase advice such as “Should I buy *Heat* now?” enters Decide. Evaluative but non-advisory wording such as “Is *Heat* cheap?” defaults to factual Inspect and may offer a decision receipt as a next action.

### Conversation scope and title branches

Maintain bounded conversation context; never treat it as a stored profile:

| Frame | Holds | Carry rule |
|---|---|---|
| Environment | Store and country | Visible defaults. A title-local comparison does not switch the Browse environment unless the user says “switch,” “from now on,” or equivalent. |
| Saved Browse frame | Lane, window, subtype, quality, price, savings, genre/rating, and sort | Compatible refinements inherit it. A title branch is pushed over it. |
| One-title branch | One canonical title/offer identity and title-local decision context | Branches never nest; selecting a second title replaces the first. |

Durability follows expressed scope. “My budget is $15” may carry to sibling title decisions with visible attribution; “show movies under $15” stays Browse-local; “for this one, I can wait a month” stays title-local. Ambiguous Decide shorthand updates only the active title and is labeled “for this title.” Sort clears on a lane or subtype pivot because it can behave as a hidden category filter; other filters carry only where their meaning and capability remain valid.

Entering Inspect or Decide from a rendered Browse row carries that snapshot's canonical title identity, market, format, and current offer. Human row numbers are bound to one rendered snapshot. After a newer snapshot makes “#3” stale or ambiguous, echo the likely title and ask for confirmation; structured consumers must use canonical identity, never position alone.

“Back” or “back to the deals” pops the title branch, restores the saved Browse criteria, refreshes live prices, and fully re-echoes the restored scope. Title-local constraints do not leak into the feed. “Start over” clears active frames and returns to the one-line orientation with visible market defaults.

### One-question information-value gate

Ask at most one blocking question before a useful result or explicit non-result state, and only when materially different executions are plausible and a default would mislead. Ask for candidate selection before a decision on an ambiguous title. For an unsupported head constraint, offer one bounded remove-versus-pivot choice rather than silently broadening. Ask before a pivot that would lose the core job, requested subtype, or every meaningful user-set constraint. Missing optional decision preferences never justify a questionnaire; use visible neutral defaults.

### Capability and degradation tiers

Validate capability before fetching, filtering, retrying, or widening:

- **Native:** execute and present ordinary results.
- **Composable:** combine documented calls or client-side filtering, label how the candidate set was formed, and avoid exhaustive-catalog claims. Movie-bundles-only and complete-series-only remain composable until completeness is verified.
- **Degraded:** only on a user-initiated pivot where the new lane and at least one meaningful user-set constraint survive. Lead with every unhonored dimension, label the preview `degraded-results`, show its actual scope, and offer bounded corrections.
- **Unsupported or unreliable:** refuse or ask the bounded choice. Never replace rental prices with purchase prices; never claim TV genre or seasons+4K was honored.

Defaults, derived values, or clearly soft context may be dropped only with visible provenance. An explicit current-request constraint or the request's core job must never disappear silently.

### Empty and error behavior

Empty means a valid effective query matched nothing; error means data could not be obtained. Keep them distinct.

- Explicit Browse **today** empty: headline that nothing was recorded as changed today and note CheapCharts source lag. Optionally show one structurally separate **Nearby: last 3 days** supplement with its own full scope. Keep the active frame on today and never widen twice.
- Vague “latest” or “recent” empty: widen once when useful, label both requested and fallback windows, and preserve both scopes.
- Exact title/date empty: answer the exact null and offer history or a wider window; do not widen silently.
- Other constrained Browse empty: preserve the scope and offer the single most useful relaxation.
- Unresolved title: return `not_found` or candidate `disambiguation`, never a fabricated match.
- Incomplete decision evidence: return `insufficient_evidence` with known facts and missing requirements, never a low-substance verdict.
- Fetch or schema failure: return `error` with a useful retry or next action, distinct from empty.

### Applied scope and source truth

Derive human and structured output from one canonical applied scope. Every result gets a compact human scope line. On ordinary refinements, provenance-tag non-obvious inherited, default-filled, or dropped dimensions; fully tag pivots, resets, composable/degraded execution, restored Browse frames, and returns after dormancy.

Structured envelopes must contain a complete self-contained `applied_scope`. Each dimension records provenance such as `user_set`, `inherited`, `default`, or `dropped_unsupported`; fallback windows, substitutions, dropped filters, and retries must also be represented. Product states are `results`, `degraded-results`, `decision`, `disambiguation`, `empty`, `insufficient_evidence`, `not_found`, `unsupported`, and `error`. Existing raw batch `--json` remains a list (or `[]` when empty). Use additive `--scoped-json` for the Browse envelope and `--decide TITLE --json` for the discriminated one-title decision envelope.

Always label the actual data source. Deals are current deals/price drops. Charts are **CheapCharts chart rankings**, Topseller is **CheapCharts cross-store top sellers**, and Recommendations is **CheapCharts recommendations**. Never present Charts, Topseller, Recommendations, Search results, or a wider fallback as if they were the requested deal feed.

### Decide receipts

A decision requires a confidently resolved offer, its current price for the relevant format tier, and at least one trustworthy historical comparator: authoritative ATL status, a parseable historical floor, or a prior comparable price. Ratings and recurrence cadence are optional. If the minimum gate fails, preserve known facts in `insufficient_evidence` and name what is missing. This is a stateless one-title decision, not feed-wide ranking: do not store a profile, create alerts, or promise monitoring.

Apply supplied per-request budget ceiling, patience, required format, and new-purchase-versus-upgrade intent. Required format is a minimum capability (`SD < HD < 4K`): 4K satisfies HD or SD, and HD satisfies SD; it does not force the CLI to price a lower tier. Omitted constraints use visible neutral defaults; do not invent taste preferences. Keep **objective deal strength** (price position, discount credibility, observed sale behavior) separate from **personal fit** (optional constraints), then combine them into the offer-specific action:

- **Buy:** buy this resolved offer now.
- **Wait:** defer for a plausibly better opportunity.
- **Skip:** this current offer fails the value or constraint threshold; it is not criticism of the title and does not mean “never buy.”

Show High, Medium, or Low confidence together with evidence coverage, conflicts, missing signals, and downgrade reasons. Confidence is not a numeric probability and is separate from deal strength. Show a broad recurrence window only when history is sufficiently deep and regular; otherwise give descriptive cadence guidance without a date estimate. Never promise an exact next-sale date.

Lead the human receipt with resolved title/offer, action, confidence, and decisive reason; then objective deal strength, personal-fit effect or neutral-default label, supported recurrence guidance, applied constraints, caveats, and useful actions. A follow-up constraint recalculates the same resolved title and highlights what changed. Keep the full timeline available as deeper evidence.

### Contract examples

| Request | Required behavior |
|---|---|
| Bare `/cheapcharts` | Show the one-line orientation once; do not run a default feed or display a menu. |
| “Today's Apple TV movie deals under $10” | Browse immediately; echo today, Apple TV/iTunes US, movies, and the price cap. |
| “Has *Heat* ever been cheaper?” | Inspect factually; no Buy / Wait / Skip. |
| “Should I buy *Heat* now?” | Decide if the evidence gate passes; otherwise return the exact non-decision state. |
| “Only 4K” | Inherit compatible Browse dimensions and label non-obvious inherited scope. |
| “What about TV?” after a movie-only scope | Clear sort and name unsupported casualties; show a degraded preview only if meaningful scope survives, otherwise ask once. |
| “Should I buy #3?” | Resolve against the displayed snapshot; confirm the title if a newer snapshot has made the row stale. |
| “Back to the deals” | Restore saved Browse criteria, refresh prices, and drop title-local context. |
| “Start over” | Clear frames and return to orientation with visible defaults. |
| “What does *Heat* cost to rent?” | Return unsupported; never substitute purchase data. |
| Explicit today with no matches | Preserve today; optionally add one separate, labeled last-three-days supplement. |

## The Bundled Script (use this first)

`scripts/deals.py` (stdlib-only, Python 3.9+) pulls Deals, enriches every candidate with the authoritative ATL flag from DetailData in parallel (8 workers, ~12s for 50 items), preserves the API's sort order, and emits a markdown table or JSON. It handles the pitfalls (status checks, DetailData vocabulary, genre validation) so you don't have to.

```
python scripts/deals.py                        # all current deals (iTunes US), ATL column
python scripts/deals.py --since 1              # only items whose price changed today
python scripts/deals.py --atl-only             # only rows at their all-time low
python scripts/deals.py --title "Fight Club"   # single-title ATL check
python scripts/deals.py --title "Fight Club" --history   # + full price-history timeline (sale windows, floor)
python scripts/deals.py --type seasons         # TV seasons
python scripts/deals.py --quality sdOnly       # strict SD results, using the SD price/date/ATL tier
python scripts/deals.py --genre horror         # movie-only genre filter; other types rejected (Pitfall #21)
python scripts/deals.py --max-price 4.99 --min-savings 3 --limit 30
python scripts/deals.py --sort greatestSavings # bundles dominate this sort (Pitfall #35)
python scripts/deals.py --exclude-bundles      # individual movies only (they carry ratings)
python scripts/deals.py --store amazon --title "Heat"   # non-iTunes: prefer --title (Pitfall #30)
python scripts/deals.py --json                 # machine-readable output for pipelines
python scripts/deals.py --scoped-json          # additive Browse envelope with applied_scope
python scripts/deals.py --decide "Heat"        # Buy / Wait / Skip receipt; --title stays factual
python scripts/deals.py --decide "Heat" --budget 10 --required-format 4k --intent upgrade
python scripts/deals.py --decide "Heat" --json # discriminated decision envelope
```

**Exit codes:** `0` deals found or a decision issued, `1` legitimate empty/non-decision state, `2` API, usage, or response-schema error. In raw batch `--json` mode, empty results emit `[]` on stdout and diagnostics go to stderr; decision and scoped Browse modes instead emit their explicit state envelope. Failed DetailData lookups are counted and reported in the table header; if all fail, exit is 2.

Default sort is `latestPricechange` (freshest drops first). Output columns: Title (links to Apple TV) | Fmt | Now | Was | Save | IMDb | RT | Date | ATL | Buy | History.

## Default Workflow

1. **"Today's drops" / "what changed today"** -> `python scripts/deals.py --since 1`. If empty, preserve today and optionally run `--since 3` only as a separately labeled Nearby supplement; CheapCharts lags Apple by hours-to-a-day ([Pitfall #14](references/PITFALLS.md#14-cheapcharts-data-lags-apples-store)). For vague “latest,” one labeled widening is allowed.
2. **"What's at its all-time low?"** -> `python scripts/deals.py --atl-only`.
3. **"Is [title] at its lowest ever?"** -> `python scripts/deals.py --title "<name>"`.
4. **Anything the script doesn't cover** (charts, recommendations, cross-store, search) -> call the API directly per the decision table below; literal curl commands in [RECIPES.md](RECIPES.md).
5. **Always check the response `status` field first** - `status=error` fails silently otherwise ([Pitfall #15](references/PITFALLS.md#15-always-check-responsestatus-before-iterating-results)).

## Decision Table

| User asks... | Do this | Why |
|---|---|---|
| "Latest deals" / "today's drops" | `deals.py --since 1` | latestPricechange sort + DetailData date verification |
| "Deals under X" | `deals.py --max-price X` | maxPrice filters server-side in the selected store/country currency |
| "Highly-rated deals" | Deals API with `imdbRating`/`rottenTomatoesRating` | Both filter server-side on Deals (NOT Recommendations, [Pitfall #23](references/PITFALLS.md#23-imdbrating-and-rottentomatoesrating-filters-work-on-dealscharts-but-not-on-recommendations)) |
| "4K / Dolby Vision / Atmos movie deals" | `deals.py --quality 4k`; filter Vision/Atmos client-side | Movie `quality=4k` works; `has4K=1` is ignored ([#39](references/PITFALLS.md#39-quality-filters-work-on-buymovies-deals-use-them-instead-of-has4k1), [#16](references/PITFALLS.md#16-has4k1-filter-does-not-work-on-dealscharts)) |
| "Newest releases on sale" | Deals `sort=releaseDate` + strip placeholder dates | Ascending only ([#11](references/PITFALLS.md#11-sortreleasedate-is-ascending-only)); bundles carry fake 2030 dates ([#12](references/PITFALLS.md#12-bundle-placeholder-dates-pollute-releasedate-sort)) |
| "What's popular / selling?" | Charts (label **CheapCharts chart rankings**) or Topseller (label **CheapCharts cross-store top sellers**) | Topseller is the only multi-store batch endpoint; neither source is a Deals feed |
| "How much is [title]?" | Search -> Prices only when `priceFollowUpItemType=buymovies` | Search resolves the IMDb ID; rental follow-ups are unsupported ([#38](references/PITFALLS.md#38-rentalmovies-is-not-a-supported-public-api-price-mode)); iTunes = Apple TV ([#19](references/PITFALLS.md#19-apple-tv--itunes-terminology)) |
| "Rental price of [title]?" | Explain that no public-API rental price is available | Deals/Charts reject `rentalmovies`; Prices silently returns purchase data ([#38](references/PITFALLS.md#38-rentalmovies-is-not-a-supported-public-api-price-mode)) |
| "Complete series deals" | Deals `itemType=seasons`, filter `isBundle=1` client-side | Season genre filter is broken ([#21](references/PITFALLS.md#21-genre-filter-is-broken-for-seasons-on-deals-charts-and-recommendations)) |
| "Recommend a [genre] movie" | Recommendations with a specific genre; label **CheapCharts recommendations** | With `genre=All` it returns chart data ([#18](references/PITFALLS.md#18-recommendationsphp-may-return-chart-data-when-no-genre-filter-is-set)); never relabel it as deals |
| "Is [title] at its ATL?" / "lowest ever?" | `deals.py --title` or DetailData `IsLowest` flags | Only DetailData exposes ATL; the flags beat parsing ([#26](references/PITFALLS.md#26-pricehdevolution--pricesdevolution-values-are-absolute-prices-not-deltas)) |
| "When was [title] on sale?" / "price history" / "when will it be on sale again?" | `deals.py --title "<name>" --history` | Render the timeline and observed cadence; give a broad recurrence window only when history is deep and regular, otherwise descriptive guidance with no estimate or promise ([EXTRAS.md](references/EXTRAS.md#seasonal-sales-calendar-itunes--apple-tv)) |
| "What just came off a sale?" | DetailData on candidates; report `priceBefore < price` rows | Sale-ended rows are "next drop target" signal ([#31](references/PITFALLS.md#31-pricebefore--price-is-signal-not-noise---it-means-the-sale-just-ended)) |
| Compare across all 4 stores | Search -> 4x Prices calls, or Topseller | Note Movies Anywhere implications ([EXTRAS.md](references/EXTRAS.md#movies-anywhere-compatibility)) |
| "Should I buy [title] now?" | Resolve the offer, fetch its quality-tier current price and trustworthy history, then use explicit decision mode | Purchase advice must produce a transparent one-title receipt or non-decision state; factual `--title` remains factual |

## Critical Pitfalls (the ones that silently break workflows)

Full list of 40 with evidence and dates: [references/PITFALLS.md](references/PITFALLS.md).

1. **DetailData speaks a different vocabulary:** `itemType=movies` or `seasons`, NOT `buymovies` - the wrong value errors, and looks like "no data" if you skip the status check ([#13](references/PITFALLS.md#13-detaildata-itemtype-is-movies-or-seasons---not-buymovies)).
2. **Always check `status` before iterating `results`** - errors return `{"status":"error"}` with an empty result shape that mimics "no deals" ([#15](references/PITFALLS.md#15-always-check-responsestatus-before-iterating-results)).
3. **`has4K=1` is silently ignored** on Deals/Charts - use `quality=4k` for a new movie Deals request; filter `has4K` client-side only for an already-fetched unfiltered response, and keep Vision/Atmos client-side ([#16](references/PITFALLS.md#16-has4k1-filter-does-not-work-on-dealscharts), [#39](references/PITFALLS.md#39-quality-filters-work-on-buymovies-deals-use-them-instead-of-has4k1)).
4. **`genre` is broken for seasons** everywhere; `deals.py` rejects `--genre` unless `--type buymovies`. Unknown genre values on movies silently return EVERYTHING at the API level, so the script rejects those too ([#21](references/PITFALLS.md#21-genre-filter-is-broken-for-seasons-on-deals-charts-and-recommendations), [#22](references/PITFALLS.md#22-genre-filter-silently-falls-back-to-all-for-unknown-values-on-buymovies)).
5. **`priceHdEvolution` values are absolute prices, NOT deltas** - the sign is only the change direction; summing them produces garbage. For "at ATL now?" use the `IsLowest` flags; for timelines use `--history`, which parses it correctly ([#26](references/PITFALLS.md#26-pricehdevolution--pricesdevolution-values-are-absolute-prices-not-deltas)).
6. **No batch DetailData** - ATL enrichment is N+1 by design; use the parallel script ([#28](references/PITFALLS.md#28-there-is-no-batch-detaildata-endpoint)).
7. **Never fabricate store URLs** - `productPageUrl`/`iTunesUrl`/`cheapChartsProductPageUrl` are in the response; guessed Apple TV slugs 404 ([#32](references/PITFALLS.md#32-never-fabricate-store-direct-urls---the-response-always-has-them)).
8. **Sort choice = category filter:** `greatestSavings` surfaces bundles, `latestPricechange` surfaces individual movies ([#35](references/PITFALLS.md#35-sortgreatestsavings-puts-bundles-at-the-top-sortlatestpricechange-puts-individual-movies-at-the-top)).
9. **Rental prices are unavailable through the public API:** Deals/Charts reject `rentalmovies`, while Prices silently returns purchases ([#38](references/PITFALLS.md#38-rentalmovies-is-not-a-supported-public-api-price-mode)).
10. **Movie `quality=` filters work on Deals:** use `quality=4k` instead of ignored `has4K=1`; seasons+4k is rejected, while supported SD modes use the SD DetailData tier ([#39](references/PITFALLS.md#39-quality-filters-work-on-buymovies-deals-use-them-instead-of-has4k1)).
11. **Currency comes from the priced response:** Deals carries batch currency and DetailData carries single-title/history currency; Search currency is not reliable ([#40](references/PITFALLS.md#40-deals-and-detaildata-carry-currency-search-currency-is-not-reliable)).

## Presentation Guidelines

1. **Lead with the effective scope** and use the canonical applied scope for both the human result and structured output. Name actual source and degradation before any rows.
2. **Always include `cheapChartsProductPageUrl`** (price-history page) and the store buy link (`productPageUrl`/`iTunesUrl` from DetailData). Never guess URLs; if a field is missing, show plain text and say "store URL unavailable".
3. **Show savings** (`priceBefore - price`, and %) and skip rows where savings <= $0 unless reporting them as "sale ended".
4. **Always include an ATL column** when data was enriched with DetailData: `ATL` = at the historical floor, `-` = ordinary sale. Standard table:

   | Title | Genre | Now | Was | Save | IMDb | ATL | Changed |
   |---|---|---:|---:|---:|---:|:-:|---|
   | [Title](cheapChartsProductPageUrl) | Genre | $X.XX | $Y.YY | $Z.ZZ (N%) | N.N | ATL | YYYY-MM-DD |

5. **"Sale ended" rows are reportable signal** ([Pitfall #31](references/PITFALLS.md#31-pricebefore--price-is-signal-not-noise---it-means-the-sale-just-ended)): when `priceBefore < price`, add a `Status` column (`on sale` / `sale ended` / `stable`) instead of rendering empty cells - these are useful manual-watch candidates, not alerts created by the skill.
6. **Ratings come from Deals candidates, not DetailData** - render IMDb/RT only for individual movies (`isMovieBundle == 0`); bundles and seasons legitimately have none ([#37](references/PITFALLS.md#37-imdbrating-and-rottentomatoesrating-are-deals-candidate-fields-not-detaildata-fields)).
7. **Filter noise:** Search with `itemType=all` returns ebooks/audiobooks/albums too - filter by `mediaType` unless asked otherwise.
8. **Mention compounding savings when relevant:** gift-card stacking and seasonal sale windows are in [EXTRAS.md](references/EXTRAS.md).
9. **Keep monetary units honest:** render the response currency; `--max-price` and `--min-savings` are amounts in the selected store/country currency, with no conversion.

## Verification Checklist

- [ ] Request was routed as Browse, Inspect, or Decide; no internal classification was exposed as a fourth lane
- [ ] Canonical applied scope and provenance agree between human and structured output
- [ ] Row references resolve against the correct rendered snapshot; stale positions are confirmed
- [ ] Capability was validated before fetch, retry, or widening; every dropped constraint is visible
- [ ] Charts, Topseller, and Recommendations are labeled by their actual source, never as Deals
- [ ] Exact empty/error semantics were preserved; a today supplement is separate and does not mutate the active frame
- [ ] Correct endpoint for the intent (Decision Table above)
- [ ] Response `status == "success"` checked before iterating (Pitfall #15)
- [ ] `itemType` vocabulary correct: `buymovies` for Deals/Charts/Prices, `movies`/`seasons` for DetailData (Pitfall #13)
- [ ] Genre values from the enum only; omitted entirely for seasons (Pitfalls #21, #22)
- [ ] Rental requests are reported as unsupported, never inferred from purchase data (Pitfall #38)
- [ ] Price, prior price, date, and ATL all come from the requested quality tier (Pitfall #39)
- [ ] ATL claims based on `priceHdIsLowest`/`priceSdIsLowest`, not parsed evolution strings (Pitfall #26)
- [ ] Fake drops filtered (`priceBefore > price`, savings > $0) and sale-ended rows reported as such (Pitfalls #9, #31)
- [ ] Buy/history links taken verbatim from the response (Pitfall #32)
- [ ] For non-iTunes stores: single-title lookups preferred over batch (Pitfall #30)
- [ ] `cheapChartsProductPageUrl` included in anything shown to the user
- [ ] Decide issued a verdict only with resolved offer + current tier price + trustworthy historical comparator
- [ ] Decision receipt separates objective deal strength, personal fit/defaults, and confidence coverage

## Files in This Skill

- [`scripts/deals.py`](scripts/deals.py) - the parallel deal/ATL finder (primary tool; unit-tested, CI-canaried)
- [`RECIPES.md`](RECIPES.md) - literal curl commands for every workflow + cron prompt templates
- [`references/API.md`](references/API.md) - full endpoint/parameter/enum/field reference
- [`references/PITFALLS.md`](references/PITFALLS.md) - all 40 empirically-verified API pitfalls
- [`references/EXTRAS.md`](references/EXTRAS.md) - gift-card stacking, Movies Anywhere, seasonal sale calendar, CheapCharts Games
- [`examples/`](examples/) - real output screenshots

## Source

- API docs: https://www.cheapcharts.com/us/ai (llms.txt) - when llms.txt and this skill disagree, llms.txt wins, then verify empirically
- Website: https://www.cheapcharts.com
- The API is free and public, designed for AI agents. No auth headers needed.
