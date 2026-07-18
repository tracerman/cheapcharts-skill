from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "skills" / "cheapcharts" / "SKILL.md").read_text(encoding="utf-8")


def test_router_exposes_exactly_three_user_lanes_and_keeps_internal_classes_internal():
    for lane in ("**Browse**", "**Inspect**", "**Decide**"):
        assert lane in SKILL
    assert "Discovery, capability checks, and conversation/session control are internal classifications" in SKILL
    assert "exactly one of three user-visible lanes" in SKILL


def test_router_contract_preserves_frames_scope_and_snapshot_identity():
    required_contract = (
        "Durability follows expressed scope",
        "Saved Browse frame",
        "One-title branch",
        "Human row numbers are bound to one rendered snapshot",
        "restores the saved Browse criteria",
        "Start over",
        "at most one blocking question",
    )
    for phrase in required_contract:
        assert phrase in SKILL


def test_router_contract_names_capability_and_exact_outcome_rules():
    for phrase in (
        "**Native:**",
        "**Composable:**",
        "**Degraded:**",
        "**Unsupported or unreliable:**",
        "Explicit Browse **today** empty",
        "Vague “latest” or “recent” empty",
        "Exact title/date empty",
        "Empty means a valid effective query matched nothing; error means data could not be obtained",
    ):
        assert phrase in SKILL


def test_contract_keeps_factual_inspect_and_raw_json_compatibility():
    assert "Factual answer only; never an unsolicited verdict" in SKILL
    assert "Existing raw batch `--json` remains a list (or `[]` when empty)" in SKILL
    assert "Use additive `--scoped-json` for the Browse envelope" in SKILL
    assert "never replace rental prices with purchase prices" in SKILL.lower()
