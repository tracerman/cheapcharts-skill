"""Fixture-driven Windows encoding smoke used by the CI workflow."""

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "skills" / "cheapcharts" / "scripts"))

import deals  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures"
DEALS_RESPONSE = json.loads((FIXTURES / "deals_response.json").read_text(encoding="utf-8"))
DETAIL_NODES = json.loads((FIXTURES / "detail_nodes.json").read_text(encoding="utf-8"))


def fixture_fetch(url, retries=2):
    del retries
    if "Deals.php" in url:
        return DEALS_RESPONSE
    if "DetailData.php" in url:
        sid = parse_qs(urlsplit(url).query)["idInStore"][0]
        node_type = "seasons" if "/seasons/" in url else "movies"
        return {"results": {node_type: DETAIL_NODES[sid]}}
    raise AssertionError(f"unexpected URL: {url}")


deals.fetch = fixture_fetch
raise SystemExit(deals.check_batch("buymovies", limit=3))
