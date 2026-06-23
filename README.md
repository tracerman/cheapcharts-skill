# CheapCharts Skill

> A free, public-API price tracker for digital movies and TV shows on iTunes/Apple TV, Amazon, Vudu, and Google Play - with an all-time-low (ATL) checker that the official CheapCharts UI doesn't expose.

Built originally for the [Hermes Agent](https://hermes-agent.nousresearch.com) ecosystem, but the script is plain Python with stdlib only and the API is just `curl` calls. Works in any agent (or just from your terminal).

*Built by [tracerman](https://github.com/tracerman) with love and coffee.*

## What it does

| Question you want answered | What to use |
|---|---|
| "What's on sale on Apple TV right now?" | `scripts/atl_check.py --type buymovies --limit 60` |
| "Is [movie] at its lowest price ever?" | `scripts/atl_check.py --title "Fight Club"` |
| "What dropped today vs yesterday?" | `scripts/atl_check.py --type buymovies --since-days 1` |
| "Best complete TV series bundle deals?" | `scripts/atl_check.py --type seasons --bundles-only` |
| "JSON output for piping into a cron job" | `scripts/atl_check.py --json` |

No API key required. CheapCharts runs a free, public, no-rate-limit GPT API designed for agents.

## Why this exists

CheapCharts has a website at cheapcharts.com that shows current prices, but it doesn't expose the all-time-low flag in the UI. The underlying DetailData endpoint does - it's just not surfaced. This skill wraps that up and gives you a script that:

- Pulls the latest deals across all four stores
- Hits DetailData in parallel (12 workers, ~12s for 50 items)
- Tells you which drops are at the historical floor (`ATL`) vs. just a typical sale
- Skips "fake drops" (manipulated `priceBefore` baselines, <$1 changes)
- Outputs JSON for cron pipelines or pretty tables for humans

## Quick start

### As a Hermes Agent skill

```bash
hermes skills install tracerman/cheapcharts-skill
```

Then in any agent session:

```
What are the latest deals on Apple TV?
```

### As a standalone Python script

```bash
git clone https://github.com/tracerman/cheapcharts-skill
cd cheapcharts-skill
python scripts/atl_check.py --type buymovies --limit 30
```

Requires Python 3.9+ (uses stdlib only: `urllib`, `concurrent.futures`, `argparse`).

### As a Claude Code slash command

Copy `claude-code/cheapcharts.md` to `~/.claude/commands/`. Then in Claude Code, type `/cheapcharts` to invoke.

### From any terminal with curl

See the [SKILL.md](SKILL.md) for direct API recipes - the skill's bundled recipes are the canonical reference.

## Example output

```
TITLE                                              NOW    WAS    SAVE  ATL  IMDb  CHANGED
15-Film Pride Pack                                $14.99 $69.99 $55.00 ATL  -     2026-06-23
Rage In The Cage: 20-Film Collection              $19.99 $89.99 $70.00 ATL  -     2026-06-23
A Better Tomorrow Trilogy                        $14.99 $39.99 $25.00 -    -     2026-06-23
Bernie                                             $4.99 $12.99  $8.00 ATL  -     2026-06-23
Werner Herzog: Radical Dreamer                     $4.99  $9.99  $5.00 ATL  -     2026-06-23
...
```

## Architecture

| File | Purpose |
|---|---|
| `SKILL.md` | Full Hermes skill manifest: all endpoints, recipes, pitfalls, presentation guidelines |
| `scripts/atl_check.py` | The CLI tool: parallel DetailData fetcher, ATL filter, single-title lookup |
| `claude-code/cheapcharts.md` | Slash command for Claude Code users |
| `examples/today-2026-06-23.md` | Sample deal report (real output, today's drops) |
| `LICENSE` | MIT |

The `DetailData` endpoint that powers the ATL check is internal to CheapCharts (not in the official gptapi docs) - it was discovered by inspecting network calls on the website. It is reliable in practice but not promised to stay stable. If it ever breaks, the script falls back gracefully and the basic `Deals` flow keeps working.

## Supported stores

| Store | Country support | Notes |
|---|---|---|
| iTunes / Apple TV | us, de, gb, fr, au, ca, at, ch, es, pt, ru, jp, tr, pl, in, cn | Best coverage. Default if no `store` set. |
| Amazon | us, de | |
| Vudu | us | |
| Google Play | us | |

iTunes and Apple TV are used interchangeably - it's the same underlying catalog. Apple rebranded iTunes Movies & TV Shows to the Apple TV app in 2019, but the purchase catalog is identical.

## Movies Anywhere compatibility

CheapCharts does not expose Movies Anywhere (MA) status in any endpoint. For multi-store comparisons, the studio-based heuristic in SKILL.md can be used:

| MA-compatible studios | NOT MA-compatible |
|---|---|
| Walt Disney Studios (Disney, Pixar, Marvel, Lucasfilm, 20th Century, Searchlight) | Paramount (incl. Republic, Miramax) |
| Warner Bros. (incl. New Line, Castle Rock, HBO theatrical) | MGM |
| Universal (incl. DreamWorks, Focus, Illumination) | Lionsgate (incl. Summit, Starz) |
| Sony Pictures (Columbia, TriStar, Screen Gems, AFFIRM, Crunchyroll theat.) | A24 (varies) |

For MA-compatible titles, the cheapest store wins regardless of where you watch. For non-MA titles, buy from the store you actually watch on.

## Contributing

Issues and PRs welcome. The most useful contributions:

- New recipes for the SKILL.md (e.g., "best horror under $5", "new 4K releases this week")
- More robust ATL detection (the current `priceHdIsLowest` flag is the most reliable signal we have)
- Multi-store parallelization (the bundled script is iTunes-only by default)
- Real examples in `examples/` - paste your actual deal report output

## License

MIT. See [LICENSE](LICENSE).
