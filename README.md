# CheapCharts Skill

> Agent skill for finding digital movie and TV deals that are actually worth buying, with parallel all-time-low checks across the stores CheapCharts tracks.

CheapCharts shows price drops. This skill checks whether a drop is a true historical low, filters out weak or inflated discounts, and gives an agent a clean answer to questions like "what noir is worth buying on Apple TV today?"

<p>
  <a href="https://github.com/tracerman/cheapcharts-skill"><img src="https://img.shields.io/github/stars/tracerman/cheapcharts-skill?style=for-the-badge&logo=github&color=181717" alt="GitHub stars"></a>
  <a href="https://github.com/tracerman/cheapcharts-skill/blob/main/LICENSE"><img src="https://img.shields.io/github/license/tracerman/cheapcharts-skill?style=for-the-badge&color=blue" alt="License"></a>
  <a href="https://github.com/tracerman/cheapcharts-skill/releases"><img src="https://img.shields.io/github/v/release/tracerman/cheapcharts-skill?style=for-the-badge&color=success&logo=semantic-release" alt="Latest release"></a>
  <a href="https://github.com/tracerman/cheapcharts-skill/actions"><img src="https://img.shields.io/github/actions/workflow/status/tracerman/cheapcharts-skill/tests.yml?style=for-the-badge&logo=github-actions&label=CI" alt="CI status"></a>
  <br>
  <a href="https://github.com/tracerman/cheapcharts-skill/issues"><img src="https://img.shields.io/github/issues/tracerman/cheapcharts-skill?style=for-the-badge&color=blue" alt="Issues"></a>
  <a href="https://github.com/tracerman/cheapcharts-skill/commits/main"><img src="https://img.shields.io/github/last-commit/tracerman/cheapcharts-skill?style=for-the-badge&color=blue" alt="Last commit"></a>
  <a href="https://www.skills.sh/tracerman/cheapcharts-skill"><img src="https://img.shields.io/badge/skills.sh-install-blueviolet?style=for-the-badge" alt="skills.sh install"></a>
  <img src="https://img.shields.io/badge/python-3.9%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.9+">
</p>

## The problem it solves

CheapCharts' deal listings tell you a title dropped in price. They don't tell you whether that price is the lowest it has *ever* been.

The reason is structural. The deals endpoints (`buymovies`, `rentalmovies`) return only price and title, with no historical data attached. All-time-low status lives in a separate `DetailData` endpoint, surfaced per title rather than as a bulk verdict across a deal list. So to know whether 50 of today's drops are at the historical floor or just a routine sale, you'd have to check 50 titles one at a time.

This skill hits that same `DetailData` endpoint in parallel and hands you the verdict for the whole batch in about 12 seconds. It also throws out the noise: manipulated "was" prices and sub-dollar changes don't count as deals.

So instead of "here's a wall of stuff that's cheaper today," you get "here's what's actually at its lowest price ever, today."

## What you can ask

Installed as a skill, you don't touch the CLI. You ask, and the agent runs the script and reads back the result:

- "What Apple TV movies are at an all-time low today?"
- "Has *The Thing* ever been cheaper than it is right now?"
- "Any horror under $5 that's actually at its lowest price ever?"
- "Best classic noir on Apple TV with a real deal on it right now?"

The script emits JSON for cron pipelines or formatted tables for humans, so the same skill drives both a scheduled "post today's ATL drops" job and an ad-hoc question.

## Demo

![Apple TV drops on 2026-06-23](skills/cheapcharts/examples/demo-2026-06-23.png)

Real output from a 2026-06-23 run against the live CheapCharts API, with each drop's `priceHdLastChangeDate` verified against the internal `DetailData` endpoint. ATL rows are highlighted in green; non-ATL rows show a `-` in the ATL column. IMDb and Rotten Tomatoes scores appear only for individual movies. Bundles, TV seasons, and complete-series bundles show `-`, because CheapCharts doesn't carry ratings for those.

## Example output

The script prints one line per title currently at its all-time low. `[BOTH]` means the price is the floor in both HD and SD; `[HD]` or `[SD]` means just one.

```
$ python scripts/atl_check.py --type buymovies --min-savings 5 --limit 40

=== 17 buymovies currently at ATL (out of 40 checked) ===

  [BOTH] Mystery Science Theater 3000: The Gizmoplex Collection | $9.99 (was $99.99, save $90.00 / 90%) | changed 2026-06-23
  [BOTH] Rage In The Cage: 20-Film Collection | $19.99 (was $89.99, save $70.00 / 78%) | changed 2026-06-23
  [BOTH] Audrey Hepburn 7-Movie Collection | $14.99 (was $69.99, save $55.00 / 79%) | changed 2026-06-23
  [BOTH] 15-Film Pride Pack | $14.99 (was $69.99, save $55.00 / 79%) | changed 2026-06-23
  [BOTH] Illumination's Ultimate 11-Movie Collection | $49.99 (was $99.99, save $50.00 / 50%) | changed 2026-06-23
  ...
```

Each line also carries an Apple TV buy link and a CheapCharts price-history link, trimmed above for width.

Combined filters (genre + max price + min savings):

```
$ python scripts/atl_check.py --genre Horror --max-price 4.99 --min-savings 3 --limit 30

=== 30 buymovies currently at ATL (out of 30 checked) [genre=Horror, maxPrice=$4.99] ===

  [BOTH] Human Resources | $1.99 (was $14.99, save $13.00 / 87%) | changed 2023-05-29
  [BOTH] Demons Never Die | $1.99 (was $12.99, save $11.00 / 85%) | changed 2023-04-18
  [BOTH] Monster on a Plane | $2.99 (was $12.99, save $10.00 / 77%) | changed 2025-08-14
  [BOTH] The Housemaid (2018) | $4.99 (was $14.99, save $10.00 / 67%) | changed 2025-05-02
  ...
```

The table in the demo screenshot above is this same data after an agent formats it into a report. The raw script output is these lines.

See [`skills/cheapcharts/examples/today-2026-06-23.md`](skills/cheapcharts/examples/today-2026-06-23.md) for a full real-world report.

## How ATL detection works

This is the part that separates a real all-time low from a sale that just looks good:

- The deals endpoints return price and title only. The ATL signal is not in them.
- `DetailData` carries `priceHdIsLowest`, the one ATL flag the API exposes directly. The script hits it in parallel (8 concurrent workers), roughly 12 seconds for 50 items versus about 150 sequential.
- Fake drops are filtered out: manipulated `priceBefore` baselines and changes under $1.
- It catches what the site misses. CheapCharts flags an all-time low the first time a title hits the floor, but does not surface concurrent ATLs. A price sitting at the floor today that was already at the floor last week won't be flagged on the site. The skill checks the live `DetailData` state, so it sees it.

## Supported stores

| Store | Country support | Coverage |
|---|---|---|
| iTunes / Apple TV | us, de, gb, fr, au, ca, at, ch, es, pt, ru, jp, tr, pl, in, cn | Full. The default, and where the script works best. |
| Amazon | us, de | Via `--store amazon`. Batch mode often returns a server-side error; single-title lookups work but data is sparser than iTunes. |
| Vudu | us | Via `--store vudu`. Data is sparser than iTunes. |
| Google Play | us | Via `--store googlePlay`. Data is sparser than iTunes. |

iTunes and Apple TV are the same underlying catalog (Apple rebranded iTunes Movies & TV Shows to the Apple TV app in 2019). The script defaults to iTunes because that's where CheapCharts has the most complete catalog and the most reliable deals endpoint. For non-iTunes stores, prefer `--title` lookups over batch mode.

## Features

- A parallel `atl_check.py` checker that verifies ATL across a deal batch in ~12s for 50 items (8 concurrent `DetailData` workers)
- Purchase (`buymovies`), rental (`rentalmovies`), and TV (`seasons`) lookups
- Script filters for genre, max price, release year, and quality (IMDb / Rotten Tomatoes filtering exists on the API but isn't a script flag, and ratings only exist for individual movies, not bundles or TV seasons)
- JSON output for cron pipelines, formatted tables for humans
- Wraps the free CheapCharts API, currently unauthenticated, so no API key is required
- A complete `SKILL.md` manifest with endpoints, recipes, and pitfalls
- A Claude Code slash command and a GitHub Actions smoke test
- Digital movie and TV deals only; physical media (Blu-ray, DVD) is out of scope

## Install

Install with whatever tool your agent uses. The skill lives at [`skills/cheapcharts/`](skills/cheapcharts/).

| Platform | Install |
|---|---|
| skills.sh (any agent) | `npx skills add tracerman/cheapcharts-skill` |
| Hermes Agent | `hermes skills install tracerman/cheapcharts-skill` |
| Claude Code | Copy the slash command into `~/.claude/commands/` (see below), then type `/cheapcharts` |
| Claude Desktop | Upload the [release zip](https://github.com/tracerman/cheapcharts-skill/releases/download/v2.2.0/cheapcharts-claude-desktop.zip) via Settings > Features > Skills |
| Plain Python | Clone and run `atl_check.py` directly (see below) |

**Claude Code slash command:**
```bash
mkdir -p ~/.claude/commands
curl -L https://raw.githubusercontent.com/tracerman/cheapcharts-skill/main/skills/cheapcharts/claude-code/cheapcharts.md \
  -o ~/.claude/commands/cheapcharts.md
```

**Claude Desktop** requires a Pro/Max/Team/Enterprise plan with code execution enabled. Download [`cheapcharts-claude-desktop.zip`](https://github.com/tracerman/cheapcharts-skill/releases/download/v2.2.0/cheapcharts-claude-desktop.zip), then Settings > Features > Skills > Upload.

**Plain Python** (no agent), Python 3.9+, standard library only:
```bash
git clone https://github.com/tracerman/cheapcharts-skill
cd cheapcharts-skill/skills/cheapcharts
python scripts/atl_check.py --title "Fight Club"
```

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

This is the canonical [Agent Skills](https://agentskills.io/specification) layout: one repo, one or more skill subdirectories under `skills/`, each with a `SKILL.md` and optional `scripts/`, `references/`, `assets/`. Tools like `npx skills add` and `hermes skills install` understand this layout.

## Contributing

Issues and PRs welcome. The most useful contributions:

- New recipes for the `SKILL.md` / `RECIPES.md`
- More robust ATL detection (`priceHdIsLowest` is the only ATL signal the API exposes directly; the only alternative is deriving it from `priceHdLastChangeDate` plus your own price history)
- Multi-store parallelization (the bundled script is iTunes-only by default)
- Real examples in `examples/`

## Links

- CheapCharts website: https://www.cheapcharts.com
- CheapCharts blog: https://www.cheapcharts.com/blog
- CheapCharts on Twitter/X: @CheapCharts_US
- CheapCharts iOS app: [App Store](https://apps.apple.com/app/cheapcharts/id772046134)
- CheapCharts Android app: [Google Play](https://play.google.com/store/apps/details?id=com.cheapcharts.app)

## License

MIT. See [LICENSE](LICENSE).

*Built by [tracerman](https://github.com/tracerman) with love and coffee.*
