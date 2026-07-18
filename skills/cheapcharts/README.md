# cheapcharts

> Browse digital movie and TV deals, inspect one title's current price or history, and decide Buy / Wait / Skip on a resolved offer across iTunes/Apple TV, Amazon, Vudu, and Google Play.

Ask a specific question directly; the skill does not force a menu first:

- **Browse:** “Today's Apple TV movie deals under $10.”
- **Inspect:** “Has *Heat* ever been cheaper?” (factual; no unsolicited verdict)
- **Decide:** “Should I buy *Heat* now?” (transparent one-title receipt when minimum evidence exists)

A bare invocation gets one short orientation line. Compatible follow-ups inherit visible scope, Browse rows retain snapshot-bound title identity, and “Back” restores the saved Browse criteria with refreshed prices. Capability loss, fallbacks, and empty/error states are explicit: rental data is never replaced with purchase prices, a today-empty last-three-days supplement remains separate, and Charts, Topseller, and Recommendations are labeled as those sources rather than as deal results.

The complete adaptive contract and decision semantics are in [`SKILL.md`](SKILL.md). Literal API calls are in [`RECIPES.md`](RECIPES.md), exact endpoint semantics are in [`references/API.md`](references/API.md), and sample output is in [`examples/`](examples/).

For install instructions, see the repo-level README: https://github.com/tracerman/cheapcharts-skill#install
