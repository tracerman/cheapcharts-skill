# CheapCharts Skill — Reddit Posts

Two posts below, ready to copy-paste into each subreddit.

---

## Post 1: r/HermesAgent

**Title:** [Skill Release] cheapcharts - all-time-low (ATL) checker for iTunes/Amazon/Vudu/Google Play

---

Built a free skill that wraps CheapCharts' public API and surfaces the all-time-low flag their UI doesn't show. Works in Hermes, Claude Code, or any agent supporting the spec.

**Install:**

```
npx skills add tracerman/cheapcharts-skill
# or just the skill from the package:
npx skills add tracerman/cheapcharts-skill --skill cheapcharts
```

**What it does:**

- Pulls latest deals from iTunes/Apple TV (the best-covered store; pass `--store amazon|vudu|googlePlay` for the others, though data is sparser and batch mode may fail there)
- Verifies each drop's change-date via CheapCharts' internal DetailData endpoint
- Flags which ones are at the historical floor (ATL) vs. just a typical sale
- Skips fake drops (manipulated `priceBefore`, sub-$1 changes)
- Parallel DetailData fetches: ~12s for 50 items

**Demo:** https://github.com/tracerman/cheapcharts-skill#demo

Real output from today's Apple TV drops (54 verified ATL deals, biggest save: $70 on Rage In The Cage 20-Film Collection).

MIT licensed, stdlib-only, no API key.

**Repo:** https://github.com/tracerman/cheapcharts-skill

Built by tracerman with love and coffee. PRs welcome, especially for multi-store parallelization and more recipes.

---

## Post 2: r/CheapCharts

**Title:** I built a free ATL checker that surfaces the all-time-low flag CheapCharts hides

---

Love the site, been using it for years. But I kept missing $4.99 drops because I couldn't tell at a glance whether $4.99 was a great price or just a back-to-normal trick.

So I built a small open-source tool that pulls the same CheapCharts data and adds the all-time-low check the website doesn't show.

**What it does:**

- Same prices as cheapcharts.com, refreshed every run
- Each deal tagged `ATL` (lowest ever) or `-` (just on sale)
- Filters out fake drops where the "was" price was inflated
- Command-line, free, MIT licensed, no signup
- Works best on iTunes/Apple TV (CheapCharts' most-covered store); Amazon, Vudu, and Google Play are supported but data is sparser

**Sample output** (real run, today):

| Title | Now | Was | Save | ATL |
|---|---|---|---|---|
| Rage In The Cage: 20-Film Collection | $19.99 | $89.99 | $70 (78%) | ATL |
| 15-Film Pride Pack | $14.99 | $69.99 | $55 (79%) | ATL |
| Bernie | $4.99 | $12.99 | $8 (62%) | ATL |
| Werner Herzog: Radical Dreamer | $4.99 | $9.99 | $5 (50%) | ATL |
| The Decline of Western Civilization I/II/III | $4.99 | $9.99 | $5 (50%) | ATL |

**Install / use:**

```
git clone https://github.com/tracerman/cheapcharts-skill
cd cheapcharts-skill/skills/cheapcharts
python scripts/atl_check.py --type buymovies --min-savings 5
```

Single-title check:

```
python scripts/atl_check.py --title "Fight Club"
```

It's also installable as an agent skill if you use Hermes, Claude Code, or any spec-compatible tool:

```
npx skills add tracerman/cheapcharts-skill
```

**Repo with full docs + real examples:** https://github.com/tracerman/cheapcharts-skill

Feedback welcome - especially on what would make this useful for r/CheapCharts regulars. Want a Discord bot, RSS feed, or weekly email digest version? Let me know.

Built by tracerman with love and coffee.
