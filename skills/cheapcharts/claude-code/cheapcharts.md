# /cheapcharts - CheapCharts Browse / Inspect / Decide alias

Route a natural CheapCharts request to Browse current deals, Inspect one title factually, or Decide Buy / Wait / Skip on one resolved offer across iTunes/Apple TV, Amazon, Vudu, and Google Play.

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

The installed skill is the primary supported invocation. To enable this optional `/cheapcharts` alias, copy it after installing the skill:

```bash
mkdir -p ~/.claude/commands
cp ~/.claude/skills/cheapcharts/claude-code/cheapcharts.md ~/.claude/commands/cheapcharts.md
```

## Usage

```
/cheapcharts                       # one-line orientation; do not run a default feed
/cheapcharts today's Apple TV movie deals under $10
/cheapcharts has Heat ever been cheaper?
/cheapcharts should I buy Heat now?
/cheapcharts since=1               # only items whose price changed in the last day
/cheapcharts type=seasons          # TV shows instead of movies
/cheapcharts store=amazon          # Amazon instead of iTunes (prefer title= there)
/cheapcharts title=Fight Club      # single-title ATL check
/cheapcharts title=Fight Club history  # + full price-history timeline
/cheapcharts genre=horror limit=20 # movie-only genre filter (any case - the script normalizes)
/cheapcharts quality=sdOnly        # strict SD tier for price, date, and ATL
/cheapcharts atl-only max_price=9.99  # ATL rows only, under $10
```

Specific natural-language requests bypass orientation and execute immediately when supported and sufficiently identified. Bare invocation says once: “I can show current movie or TV deals, inspect a title's price history, or help decide Buy / Wait / Skip—what are you looking for?” Do not expose discovery, capability, or session control as additional menu lanes.

## What this command does

1. Infers the requested user job: a set/feed is **Browse**, a factual named-title question is **Inspect**, and purchase advice is **Decide**. Factual `title=` remains factual; do not attach an unsolicited verdict.
2. Reads explicit arguments and maps them to script flags:

   | Alias argument | Script flag |
   |---|---|
   | `title=` | `--title` |
   | `history` | `--history` (requires `title=`) |
   | `type=` | `--type` |
   | `store=` / `country=` | `--store` / `--country` |
   | `since=` / `limit=` | `--since` / `--limit` |
   | `sort=` | `--sort` |
   | `genre=` | `--genre` (movies only; Pitfall #21) |
   | `quality=` | `--quality` |
   | `max_price=` / `min_savings=` | `--max-price` / `--min-savings` |
   | `release_year=` | `--release-year` |
   | `exclude-bundles` | `--exclude-bundles` |
   | `atl-only` | `--atl-only` |
   | `json` | `--json` |
   | `scoped-json` | `--scoped-json` (additive Browse envelope) |
   | `decide=` | `--decide` |
   | `budget=` / `patience=` | `--budget` / `--patience` |
   | `required_format=` / `intent=` | `--required-format` / `--intent` |

3. Runs `python scripts/deals.py <flags>`. Browse pulls the requested deal candidates and verifies ATL evidence in parallel. Inspect uses factual title/history mode. Decide uses `--decide TITLE` with any supplied constraints and returns a verdict only when title, current quality-tier price, and a trustworthy historical comparator are available.
4. Relays the lane-appropriate output with a visible effective-scope receipt. Preserve existing Browse exit codes: `0` deals found, `1` legitimately empty, `2` API/usage/response-schema error. Raw batch `--json` remains a list and emits `[]` on empty; `--scoped-json` is the additive Browse envelope; `--decide TITLE --json` is the discriminated decision envelope whose explicit `state` distinguishes decision, disambiguation, insufficient evidence, not found, and error.

Compatible elliptical refinements inherit the saved Browse frame and visibly identify non-obvious inherited values. Selecting a row pushes one title branch with snapshot-bound canonical identity. A stale `#3` after a newer result requires title confirmation. “Back” restores saved Browse criteria and refreshes live prices; “Start over” clears active frames and returns to orientation. Expressed scope controls durability, so title-local patience does not leak into the feed.

Ask at most one blocking question, only when a default would materially misroute or misrepresent the request. Validate capability before fetching: native calls run normally; composable scopes disclose candidate coverage; partial pivots may show a limitation-first `degraded-results` preview only when meaningful scope survives; unsupported head constraints get one bounded remove-versus-pivot choice. Never substitute purchase data for rental prices or claim TV genre or seasons+4K was honored.

## When to use

- "What's on sale on Apple TV?"
- "What just dropped today?" (use `since=1`)
- "Is [movie] at its lowest price ever?" (use `title=`)
- "Best 4K movie deals under $10?" (use `quality=4k`; movie batches only)
- "Complete series bundles on sale" (use `type=seasons`)
- "Should I buy Heat now?" (use `decide=Heat`; add only the constraints the user supplied)

## Output format

The script emits this table (Title and Buy link to the Apple TV purchase page, History to the CheapCharts price-history page):

```
| Title | Fmt | Now | Was | Save | IMDb | RT | Date | ATL | Buy | History |
|---|:-:|---:|---:|---|:-:|:-:|---|:-:|:-:|:-:|
| [Bernie](...) | HD | $4.99 | $12.99 | $8.00 (62%) | 6.8 | 79 | 2026-06-23 | ✓ | [Buy](...) | [History](...) |
```

`ATL` = `✓` means the current price equals the all-time low across CheapCharts' tracked history. IMDb/RT show `-` for bundles and TV seasons (the API doesn't carry ratings for those).

## Deeper workflows

The parent skill's `SKILL.md` has the decision table for API calls the script doesn't cover (charts, recommendations, cross-store comparison), and `references/PITFALLS.md` documents all 40 known API gotchas. Repo: https://github.com/tracerman/cheapcharts-skill

## Caveats

- The DetailData endpoint (used for ATL detection) is unofficial - discovered by inspecting CheapCharts' website network calls. Reliable in practice, not promised stable.
- CheapCharts' price data lags Apple's store by hours to a day. An empty today result may justify one separately labeled `since=3` Nearby supplement.
- For an explicit today request, `since=3` is only a separate labeled **Nearby: last 3 days** supplement. It never replaces today, changes the active frame, or runs more than once. Vague “latest” may widen once with both scopes shown.
- Empty means a supported effective query matched nothing; error means data could not be obtained. Keep them distinct. Exact title/date nulls stay exact and are never widened silently.
- Charts, Topseller, and Recommendations must be labeled **CheapCharts chart rankings**, **CheapCharts cross-store top sellers**, and **CheapCharts recommendations**. They are not Deals results.
- The `Was` column comes from the API's `priceBefore` and can occasionally be an inflated baseline. Use `--min-savings` to skip trivial drops, and sanity-check against the History link before buying.
- Public CheapCharts endpoints do not expose verified rental prices. `type=rentalmovies` returns a clear capability error; do not substitute purchase data from Prices (Pitfall #38).
- `quality=sd` and `quality=sdOnly` use the SD price/date/ATL tier; other supported quality modes prefer HD and fall back to SD only when HD is unavailable. `quality=4k` is movie-only; seasons+4k is rejected before networking (Pitfall #39).
- Monetary output uses the Deals/DetailData response currency. `max_price=` and `min_savings=` amounts are in the selected store/country currency; the script never converts currencies (Pitfall #40).
