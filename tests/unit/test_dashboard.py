"""The renderer that stands in for the Power BI report, with no database."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from cybersum.dashboard import _as_mapping, _origins_table, render_page
from cybersum.notify import STATUS_COLORS, VALID_STATUSES

ROW = {
    "report_date": date(2026, 8, 14),
    "report_content": "1: EXECUTIVE SUMMARY\nAll quiet.\n\n2: TECHNICAL BRIEF\n- 2,592 blocked\n",
    "status_code": "STATUS C",
    "top_5_origins": {"United States": 414, "China": 215},
    "total_tokens": 3683,
    "model_version": "gpt-4o",
    "created_at": datetime(2026, 8, 14, 8, 0, 0),
}


def test_report_content_is_escaped_before_it_reaches_the_page():
    """The briefing is model-generated text going into HTML. Same threat, and
    the same defence, as notify.render_html."""
    hostile = dict(ROW, report_content="<script>alert('xss')</script>")
    page = render_page([hostile])

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert" in page


def test_a_hostile_origin_key_is_escaped_too():
    page = render_page([dict(ROW, top_5_origins={"<img src=x onerror=1>": 5})])
    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


def test_an_empty_result_set_renders_an_empty_state_rather_than_raising():
    """Before the first run there is no row. That is a normal state, not an
    error, and the page has to say so."""
    page = render_page([])
    assert "make demo" in page
    assert "<html" in page


@pytest.mark.parametrize("status", VALID_STATUSES)
def test_every_status_in_the_vocabulary_gets_its_own_badge_colour(status):
    page = render_page([dict(ROW, status_code=status)])
    assert STATUS_COLORS[status] in page
    assert f">{status}<" in page


def test_an_unrecognised_status_still_renders():
    """A status the vocabulary does not cover must not blank the page: the
    briefing is worth showing even when the side channel produced nonsense."""
    page = render_page([dict(ROW, status_code="STATUS Q")])
    assert "STATUS Q" in page
    assert "All quiet." in page


def test_the_briefing_is_rendered_verbatim_and_not_as_markdown():
    """The renderer does no reflow and parses no Markdown. That is the
    downstream fact the production prompt is written against -- see
    docs/prompts.md -- so it is asserted rather than assumed."""
    page = render_page([ROW])

    assert "white-space: pre-wrap" in page
    # The literal text, newlines intact, inside the pre block.
    body = re.search(r'<pre class="briefing">(.*?)</pre>', page, re.DOTALL)
    assert body is not None
    assert "1: EXECUTIVE SUMMARY\nAll quiet." in body.group(1)


def test_the_page_loads_nothing_from_the_network():
    """A README screenshot should not depend on a CDN, and a renderer that
    fetches nothing cannot leak the briefing to whoever it fetched from. The
    check is for resource-loading constructs, not for the string 'https' --
    briefings legitimately quote URLs, and those are inert escaped text."""
    page = render_page([dict(ROW, report_content="see https://www.site1.org for detail")])

    for construct in ("<script", "<link", "<iframe", " src=", "@import", "url("):
        assert construct not in page, f"page pulls in a resource via {construct!r}"

    assert "https://www.site1.org" in page  # quoted in the prose, and only there


def test_the_page_and_the_json_endpoint_are_built_from_the_same_columns():
    """The page must not display a field the endpoint does not return. Both
    come from storage.fetch_latest_report, and its SELECT list is the contract
    Power BI was written against."""
    source = Path("src/cybersum/storage.py").read_text()
    select = re.search(
        r"def fetch_latest_report.*?SELECT(.*?)FROM daily_security_reports",
        source,
        re.DOTALL,
    )
    assert select is not None
    columns = {c.strip() for c in select.group(1).replace("\n", " ").split(",")}

    assert columns == set(ROW), (
        "fetch_latest_report's columns changed; the renderer's fixture and the "
        "page built from it have to follow."
    )


def test_origins_survive_arriving_as_a_json_string():
    """JSONB comes back as a dict, but a driver or a fixture may hand over the
    text. Rendering must not depend on which."""
    assert _as_mapping('{"US": 3}') == {"US": 3}
    assert _as_mapping(None) == {}
    assert _as_mapping("not json") == {}

    assert "US" in _origins_table('{"US": 3}')
    assert "No origin breakdown" in _origins_table(None)


def test_origins_are_ordered_by_volume():
    table = _origins_table({"China": 215, "United States": 414})
    assert table.index("United States") < table.index("China")


def test_the_generation_time_is_shown_to_the_minute():
    """A daily job's microseconds are noise, and the full repr wraps onto a
    second line in the sidebar."""
    page = render_page([ROW])
    assert "2026-08-14 08:00" in page
    assert "08:00:00" not in page


def test_the_json_payload_serialises_dates_the_way_the_azure_route_does():
    """function_app.py dumps with default=str. A date object would otherwise
    raise, and the endpoint returns dates in two of its seven columns."""
    assert json.dumps([ROW], default=str)
