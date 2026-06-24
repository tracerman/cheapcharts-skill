# /cheapcharts - CheapCharts deal lookup

Query the CheapCharts public API for the latest price drops on iTunes/Apple TV, Amazon, Vudu, and Google Play. Returns a formatted deal table with all-time-low (ATL) flags.

## Usage

```
/cheapcharts                       # latest Apple TV (iTunes US) deals
/cheapcharts type=seasons          # TV shows instead of movies
/cheapcharts store=amazon          # Amazon instead of iTunes
/cheapcharts title=Fight Club      # single-title ATL check
/cheapcharts max=20 genre=horror   # top 20 horror under ATL filter
/cheapcharts since=1               # only items changed in the last 1 day
```

## What this command does

1. Pulls the top 50-80 items from the CheapCharts Deals API for the requested store and item type, sorted by `latestPricechange`.
2. Calls the internal DetailData endpoint for each candidate to verify the actual `priceHdLastChangeDate` and check the `priceHdIsLowest` / `priceSdIsLowest` ATL flags. These calls run in parallel (12 workers, ~12s for 50 items).
3. Filters to items matching the user's request (today, specific title, genre, etc.) and skips "fake drops" (savings <= $1).
4. Returns a markdown table with: Title, Now, Was, Save, ATL, IMDb (when available), and change date.

## When to use

- "What's on sale on Apple TV?"
- "Is [movie] at its lowest price ever?"
- "What just dropped today?"
- "Best 4K movie deals under $10?"
- "Complete series bundles on sale"

## Examples

```
/cheapcharts
/cheapcharts type=seasons since=3
/cheapcharts title=Bernie store=itunes
/cheapcharts max=15 genre=action max_price=4.99
```

## Output format

```
| Title | Genre | Now | Was | Save | IMDb | ATL | Changed |
|---|---|---:|---:|---:|---:|:-:|---|
| 15-Film Pride Pack | Drama | $14.99 | $69.99 | $55.00 (79%) | - | ATL | 2026-06-23 |
| Bernie | Drama | $4.99 | $12.99 | $8.00 (62%) | - | ATL | 2026-06-23 |
| A Better Tomorrow Trilogy | Action | $14.99 | $39.99 | $25.00 (63%) | - | - | 2026-06-23 |
```

## How it works (for the agent)

The complete workflow with exact curl commands, all known pitfalls (15+), and the full presentation guide is in the parent skill's SKILL.md. This slash command is a thin wrapper that:

1. Reads the user's request to extract `type`, `store`, `title`, `max`, `since`, `genre`, `max_price`
2. Calls the bundled `scripts/deals.py` with the right flags
3. Formats the result as a markdown table

If the user wants to invoke the underlying scripts directly, point them at the parent repo: https://github.com/tracerman/cheapcharts-skill

## Caveats

- The DetailData endpoint (used for ATL detection) is unofficial - discovered by inspecting CheapCharts' website network calls. Reliable in practice, not promised stable.
- CheapCharts' price data lags Apple's store by hours to a day. "Today" queries may sometimes return yesterday's drops during early US morning hours.
- "Fake drops" (where `priceBefore` is inflated) are filtered, but the filter is heuristic. Always sanity-check the `Was` column against recent price history on cheapcharts.com before buying.
