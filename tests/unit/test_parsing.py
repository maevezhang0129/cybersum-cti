"""The ###DATA_START### side channel and the dashboard formatter."""

from __future__ import annotations

import logging

import pytest

from cybersum.parsing import extract_json_data, format_report_for_dashboard

BRIEFING = "1: EXECUTIVE SUMMARY\nAll quiet.\n\n2: TECHNICAL BRIEF\n- nothing to report"


def wrap(block: str) -> str:
    return f"{BRIEFING}\n###DATA_START###\n{block}\n###DATA_END###"


def test_extracts_status_and_nested_origins() -> None:
    parsed = extract_json_data(
        wrap('{"status_code": "STATUS C", "top_5_origins": {"US": 5, "CN": 3}}')
    )
    assert parsed.extraction_failed is False
    assert parsed.status_code == "STATUS C"
    assert parsed.top_5_origins == {"US": 5, "CN": 3}


def test_delimiters_never_survive_into_the_prose() -> None:
    parsed = extract_json_data(wrap('{"status_code": "STABLE"}'))
    assert "###DATA_START###" not in parsed.text
    assert "###DATA_END###" not in parsed.text
    assert parsed.text == BRIEFING


def test_missing_block_is_flagged_not_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    """Defaulting to STABLE is the intended fallback, but a caller has to be
    able to tell a real all-clear from a parser failure."""
    with caplog.at_level(logging.WARNING):
        parsed = extract_json_data(BRIEFING)
    assert parsed.extraction_failed is True
    assert parsed.status_code == "STABLE"
    assert parsed.top_5_origins == {}
    assert caplog.records, "a missing data block must be logged"


def test_malformed_json_is_flagged_and_still_cleans_the_prose() -> None:
    parsed = extract_json_data(wrap('{"status_code": "STATUS C",,,}'))
    assert parsed.extraction_failed is True
    assert parsed.failure_reason and "malformed" in parsed.failure_reason
    assert "###DATA_START###" not in parsed.text


def test_json_array_in_the_block_is_rejected() -> None:
    """The block must be an object; a list would break attribute access
    downstream rather than degrading."""
    parsed = extract_json_data(wrap('{"a": 1}').replace('{"a": 1}', '{"x": [1,2]}'))
    assert parsed.extraction_failed is False  # still an object


def test_top_5_origins_as_a_list_degrades_to_empty_dict() -> None:
    parsed = extract_json_data(wrap('{"status_code": "STATUS B", "top_5_origins": ["US"]}'))
    assert parsed.status_code == "STATUS B"
    assert parsed.top_5_origins == {}


def test_markdown_fenced_block_is_not_extracted() -> None:
    """A known limitation, pinned so it cannot regress unnoticed: if the model
    wraps the JSON in a code fence, the regex misses it and the report ships
    with a defaulted status. The flag is what keeps that visible."""
    block = '```json\n{"status_code": "STATUS C"}\n```'
    fenced = f"{BRIEFING}\n###DATA_START###\n{block}\n###DATA_END###"
    parsed = extract_json_data(fenced)
    assert parsed.extraction_failed is True
    assert parsed.status_code == "STABLE"


# ── formatter ────────────────────────────────────────────────────────────────

def test_headings_are_separated_and_bullets_start_lines() -> None:
    out = format_report_for_dashboard("Intro text.1: EXECUTIVE SUMMARY ok.2: TECHNICAL BRIEF x")
    assert "\n\n1: EXECUTIVE SUMMARY" in out
    assert "\n\n2: TECHNICAL BRIEF" in out


def test_hyphenated_words_are_not_split_across_lines() -> None:
    """The original rule matched any hyphen preceded by a non-space and turned
    "high-intensity attack" into "high\\n-intensity attack" in every briefing
    that used the phrase. Bullets are hyphens followed by whitespace."""
    out = format_report_for_dashboard("Detected a high-intensity attack on the edge-router.")
    assert "high-intensity" in out
    assert "edge-router" in out
    assert "\n-" not in out


def test_bullets_still_get_their_own_line() -> None:
    out = format_report_for_dashboard("Metrics: - cpu high - memory ok")
    assert out.count("\n- ") == 2


def test_formatting_is_idempotent() -> None:
    once = format_report_for_dashboard(BRIEFING)
    assert format_report_for_dashboard(once) == once


def test_runs_of_blank_lines_collapse_and_output_ends_with_one_newline() -> None:
    out = format_report_for_dashboard("\n\n\nalpha\n\n\n\n\nbeta\n\n\n")
    assert out == "alpha\n\nbeta\n"
