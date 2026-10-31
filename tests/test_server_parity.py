"""The proxy's validator (site/api/_lib/validate.js) and the app's parser agree.

The site test suite writes test/fixtures/validated.json: typical good and bad replies from
each model in the free fallback chain, plus what the server made of them. Here the app checks:
  * every reply the server passes as a binding parses into a Proposal,
  * every reply the server refused for safety is also refused by the app on its own
    (raw model text straight into the app, as if the server had not checked it).
"""

import json
import os
from pathlib import Path

import pytest

from assist.ai_binder import AIBinderError, Proposal, parse_batch

SITE = Path(os.environ.get("KEYWARE_SITE") or Path(__file__).resolve().parents[1] / "site")
FIXTURE = SITE / "test" / "fixtures" / "validated.json"
ROWS = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else []

pytestmark = pytest.mark.skipif(not ROWS, reason="site fixture not found (run node --test in site/)")


def _ids(rows):
    return [f"{r['model']}-{r['name']}" for r in rows]


OK = [r for r in ROWS if r["status"] == "ok"]
REFUSED = [r for r in ROWS if r["status"] == "refusal" and '"not_binding"' not in (r["reply"] or "")]


@pytest.mark.parametrize("row", OK, ids=_ids(OK))
def test_server_approved_replies_parse_in_the_app(row):
    items = parse_batch(row["reply"])
    assert items and all(isinstance(i, Proposal) for i in items)


@pytest.mark.parametrize("row", REFUSED, ids=_ids(REFUSED))
def test_app_refuses_unsafe_replies_on_its_own(row):
    try:
        items = parse_batch(row["raw"])
    except AIBinderError:
        return
    # A batch may keep its safe items, but nothing unsafe may come through as a Proposal.
    text = json.dumps([getattr(i, "action", None).__dict__ if isinstance(i, Proposal) else None for i in items], default=str)
    assert not any(isinstance(i, Proposal) for i in items), text
