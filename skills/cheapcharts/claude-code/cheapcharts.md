# /cheapcharts - CheapCharts deal lookup

Query the CheapCharts public API for the latest price drops on iTunes/Apple TV, Amazon, Vudu, and Google Play. Returns a markdown deal table with all-time-low (ATL) flags.

## Requirements

This command drives the bundled `scripts/deals.py` from the CheapCharts skill. Locate it in this order:

1. `~/.claude/skills/cheapcharts/scripts/deals.py` (skill installed for Claude Code)
2. A local clone of the repo (look for `skills/cheapcharts/scripts/deals.py`)
3. Neither found: clone it first, then run from the clone:
   ```bash
   git clone https://github.com/tracerman/cheapcharts-skill /tmp/cheapcharts-skill
   cd /tmp/cheapcharts-skill/skills/cheapcharts
   ```

Python 3.9+ with the standard library only. No API key needed.

## Usage

```
/cheapcharts                       # latest Apple TV (iTunes US) deals, ATL column
/cheapcharts since=1               # only items whose price changed in the last day
/cheapcharts type=seasons          # TV shows instead of movies
/cheapcharts store=amazon          # Amazon instead of iTunes (prefer title= there)
/cheapcharts title=Fight Club      # single-title ATL check
/cheapcharts title=Fight Club history  # + full price-history timeline
/cheapcharts genre=horror limit=20 # genre filter (any case - the script normalizes)
/cheapcharts atl-only max_price=9.99  # ATL rows only, under $10
```

## What this command does

1. Reads the user's request and maps it to script flags: `type=` -> `--type`, `store=` -> `--store`, `title=` -> `--title`, `since=` -> `--since`, `genre=` -> `--genre`, `limit=` -> `--limit`, `max_price=` -> `--max-price`, `atl-only` -> `--atl-only`, `history` -> `--history` (with `title=`; use for "when was it on sale" / "price history" questions).
2. Runs `python scripts/deals.py <flags>`. The script pulls the top deals sorted by `latestPricechange`, then verifies each candidate against the internal DetailData endpoint in parallel (8 workers, ~12s for 50 items) to get the authoritative `priceHdIsLowest` / `priceSdIsLowest` ATL flags and real change dates.
3. Relays the script's markdown table. Exit codes: `0` deals found, `1` legitimately empty result (say so - don't call it an error), `2` API/usage error (report stderr).

## When to use

- "What's on sale on Apple TV?"
- "What just dropped today?" (use `since=1`)
- "Is [movie] at its lowest price ever?" (use `title=`)
- "Best 4K movie deals under $10?"
- "Complete series bundles on sale" (use `type=seasons`)

## Output format

The script emits this table (Title and Buy link to the Apple TV purchase page, History to the CheapCharts price-history page):

```
| Title | Fmt | Now | Was | Save | IMDb | RT | Date | ATL | Buy | History |
|---|:-:|---:|---:|---|:-:|:-:|---|:-:|:-:|:-:|
| [Bernie](...) | HD | $4.99 | $12.99 | $8.00 (62%) | 6.8 | 79 | 2026-06-23 | ✓ | [Buy](...) | [History](...) |
```

`ATL` = `✓` means the current price equals the all-time low across CheapCharts' tracked history. IMDb/RT show `-` for bundles and TV seasons (the API doesn't carry ratings for those).

## Deeper workflows

The parent skill's `SKILL.md` has the decision table for API calls the script doesn't cover (charts, recommendations, cross-store comparison), and `references/PITFALLS.md` documents all 37 known API gotchas. Repo: https://github.com/tracerman/cheapcharts-skill

## Caveats

- The DetailData endpoint (used for ATL detection) is unofficial - discovered by inspecting CheapCharts' website network calls. Reliable in practice, not promised stable.
- CheapCharts' price data lags Apple's store by hours to a day. "Today" queries may return yesterday's drops during early US morning hours; `since=3` is a sensible fallback.
- The `Was` column comes from the API's `priceBefore` and can occasionally be an inflated baseline. Use `--min-savings` to skip trivial drops, and sanity-check against the History link before buying.
