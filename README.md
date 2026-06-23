# CheapCharts Skill (by tracerman)

> A free, public-API price tracker for digital movies and TV shows on iTunes/Apple TV, Amazon, Vudu, and Google Play - with an all-time-low (ATL) checker that the official CheapCharts UI doesn't expose.

*Built by [tracerman](https://github.com/tracerman) with love and coffee.*

## What is this

This is an **agent skill** that lets any AI agent (Hermes, Claude Code, OpenAI Codex, Cursor, etc.) look up movie and TV show prices across all four major US digital stores, and check whether a given drop is at the historical floor (all-time low / ATL).

It wraps the [CheapCharts public API](https://www.cheapcharts.com/us/ai) (no auth, no rate limits) and includes:

- A complete `SKILL.md` manifest with all endpoints, recipes, and pitfalls
- A parallel `atl_check.py` script that finds ATL deals in ~12 seconds for 50 items
- A real example deal report from today
- A Claude Code slash command
- A GitHub Actions smoke test

## Install

The skill lives at [`skills/cheapcharts/`](skills/cheapcharts/). Install it with whatever skill tool your agent uses:

**Vercel / skills.sh (any agent):**
```bash
npx skills add tracerman/cheapcharts-skill
# or just this one skill:
npx skills add tracerman/cheapcharts-skill --skill cheapcharts
```

**Hermes Agent:**
```bash
hermes skills install tracerman/cheapcharts-skill
```

**Claude Code (slash command):**
```bash
mkdir -p ~/.claude/commands
curl -L https://raw.githubusercontent.com/tracerman/cheapcharts-skill/main/skills/cheapcharts/claude-code/cheapcharts.md \
  -o ~/.claude/commands/cheapcharts.md
```
Then type `/cheapcharts` in Claude Code.

**Plain Python (no agent):**
```bash
git clone https://github.com/tracerman/cheapcharts-skill
cd cheapcharts-skill/skills/cheapcharts
python scripts/atl_check.py --title "Fight Club"
```
Requires Python 3.9+ (uses stdlib only).

## Quick example

```
$ python scripts/atl_check.py --type buymovies --min-savings 5

TITLE                            NOW     WAS     SAVE    ATL  IMDb  CHANGED
Rage In The Cage: 20-Film...    $19.99  $89.99  $70.00  ATL  -     2026-06-23
15-Film Pride Pack              $14.99  $69.99  $55.00  ATL  -     2026-06-23
A Better Tomorrow Trilogy       $14.99  $39.99  $25.00  -    -     2026-06-23
Bernie                           $4.99  $12.99   $8.00  ATL  -     2026-06-23
Werner Herzog: Radical Dreamer   $4.99   $9.99   $5.00  ATL  -     2026-06-23
...
```

See [`skills/cheapcharts/examples/today-2026-06-23.md`](skills/cheapcharts/examples/today-2026-06-23.md) for a full real-world report.

## Repo structure (skill package)

```
cheapcharts-skill/
├── README.md                          # this file (install + overview)
├── LICENSE                            # MIT
├── .github/workflows/tests.yml        # CI smoke test
└── skills/
    └── cheapcharts/                   # the skill itself
        ├── SKILL.md                   # manifest (frontmatter + body)
        ├── RECIPES.md                 # literal curl recipes
        ├── scripts/atl_check.py       # parallel ATL checker
        ├── examples/                  # sample deal reports
        └── claude-code/cheapcharts.md # slash command
```

This is the canonical [Agent Skills](https://agentskills.io/specification) layout: a "skill package" repo where each skill lives in its own subdirectory under `skills/`. Tools like `npx skills add` and `hermes skills install` understand this layout.

## Why this exists

CheapCharts has a website that shows current prices, but it doesn't expose the all-time-low flag in the UI. The underlying DetailData endpoint does - it's just not surfaced. This skill wraps that up and gives you a script that:

- Pulls the latest deals across all four stores
- Hits DetailData in parallel (12 workers, ~12s for 50 items)
- Tells you which drops are at the historical floor (`ATL`) vs. just a typical sale
- Skips "fake drops" (manipulated `priceBefore` baselines, <$1 changes)
- Outputs JSON for cron pipelines or pretty tables for humans

## Supported stores

| Store | Country support |
|---|---|
| iTunes / Apple TV | us, de, gb, fr, au, ca, at, ch, es, pt, ru, jp, tr, pl, in, cn |
| Amazon | us, de |
| Vudu | us |
| Google Play | us |

iTunes and Apple TV are used interchangeably - same underlying catalog. Apple rebranded iTunes Movies & TV Shows to the Apple TV app in 2019.

## Contributing

Issues and PRs welcome. The most useful contributions:

- New recipes for the SKILL.md / RECIPES.md
- More robust ATL detection (current `priceHdIsLowest` flag is the most reliable signal we have)
- Multi-store parallelization (bundled script is iTunes-only by default)
- Real examples in `examples/`

## License

MIT. See [LICENSE](LICENSE).
