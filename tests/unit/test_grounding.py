"""The numeric grounding check.

Calibrated against the fifteen real briefings in evaluation/outputs/, not against
imagined output. The acceptance test is the last one in this file: it must catch
the failure the whole thing exists for.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from cybersum.grounding import Facts, Kind, check_grounding, extract_figures

OUTPUTS = pathlib.Path(__file__).resolve().parents[2] / "evaluation" / "outputs"

CONTEXT = {
    "period": "Last 24 Hours",
    "security_summary": {
        "total_blocked_events": 11716,
        "top_attacks": [{"target_host": "www.site1.org", "attacker_country": "US",
                         "block_count": 2413}],
        "ddos_status": {"event_timestamp": "2026-03-10 00:30:00+00:00",
                        "health_status": "CRITICAL", "risk_score": "62.0",
                        "malicious_percent": "45.0"},
    },
    "infrastructure_health": {
        "abnormal_services": [{"service_name": "Main Web Portal", "status_text": "Paused"}],
        "azure_resource_usage": [{"time_bucket": "2026-03-10 18:00",
                                  "avg_memory_mb": "10029.99", "max_cpu_load": "100.0"}],
    },
    "historical_trends": {"data": [{"d": "03-09", "c": "1500", "t": "block"}]},
}


def check(prose: str):
    return check_grounding(prose, CONTEXT)


# ── the basic contract ───────────────────────────────────────────────────────

def test_a_figure_present_in_the_context_is_grounded() -> None:
    assert check("A total of 11,716 events were blocked.").ok


def test_a_figure_absent_from_the_context_is_flagged() -> None:
    result = check("A total of 6,104 events were blocked.")
    assert not result.ok
    assert result.ungrounded[0].raw == "6,104"


def test_rounding_is_accepted() -> None:
    """The model writes 10,030 for 10,029.99. Rejecting that would flag prose
    that is entirely correct."""
    assert check("Memory peaked at 10,030 MB.").ok


def test_rounding_beyond_tolerance_is_not_accepted() -> None:
    assert not check("Memory peaked at 11,500 MB.").ok


def test_thousands_separators_do_not_matter() -> None:
    assert check("Blocked: 11716 events.").ok


# ── categories ───────────────────────────────────────────────────────────────

def test_a_ratio_in_the_data_may_be_a_percentage_in_the_prose() -> None:
    """malicious_ratio arrives as 0.45; the briefing says 45%."""
    facts = Facts.from_context({"ratio": 0.45})
    assert facts.has_number(45.0)


def test_clock_times_are_matched_against_the_data() -> None:
    assert check("Peak CPU load reached 100% at 18:00.").ok
    assert not check("Peak CPU load reached 100% at 04:00.").ok


def test_a_year_from_a_timestamp_is_known() -> None:
    """Timestamps in the context carry a year the prose repeats as a bare
    number: "March 10, 2026"."""
    assert check("Report for March 10, 2026 covering all systems.").ok


def test_dates_are_matched_against_the_trend_data() -> None:
    assert check("A spike was observed on 03-09.").ok
    assert not check("A spike was observed on 07-22.").ok


# ── things that are not claims about the data ────────────────────────────────

def test_digits_inside_hostnames_are_not_figures() -> None:
    assert [f.raw for f in extract_figures("Traffic to www.site1.org and api.site2.org.")] == []


def test_digits_inside_urls_are_not_figures() -> None:
    assert extract_figures("See https://www.site1.org/path/2999 for detail.") == []


def test_list_markers_are_not_figures() -> None:
    assert extract_figures("1. First item\n2. Second item") == []


def test_the_reporting_window_is_not_a_finding() -> None:
    """Every briefing opens with "in the last 24 hours". That is a property of
    the request, not a measurement, and flagging it would put a false positive
    in every single report."""
    assert check("In the last 24 hours, the posture has been stable.").ok
    assert check("Over the last 90 days, blocks trended upward.").ok


def test_the_status_vocabulary_is_not_a_figure() -> None:
    assert extract_figures("CURRENT SYSTEM STATUS: STATUS C") == []


def test_small_counts_are_skipped_rather_than_checked() -> None:
    """"the top 5 origins", "3 services" -- ordinals and counts of visible
    things. Checking them produces noise, so they are counted separately and
    the denominator stays honest."""
    result = check("The top 5 origins across 3 services.")
    assert result.ok
    assert len(result.skipped) == 2
    assert result.checked == 0


# ── extraction detail ────────────────────────────────────────────────────────

def test_a_clock_is_not_read_as_two_quantities() -> None:
    figures = extract_figures("Peak at 18:00 today.")
    assert [f.kind for f in figures] == [Kind.CLOCK]


def test_a_percentage_keeps_its_sign() -> None:
    figures = extract_figures("Malicious traffic was 45%.")
    assert figures[0].kind is Kind.PERCENTAGE
    assert figures[0].value == 45.0


def test_the_snippet_locates_the_figure_for_a_human() -> None:
    figure = check("A total of 6,104 events were blocked yesterday.").ungrounded[0]
    assert "6,104" in figure.snippet
    assert "blocked" in figure.snippet


def test_summary_names_what_could_not_be_traced() -> None:
    summary = check("We saw 6,104 events.").summary()
    assert "6,104" in summary
    assert "1 of 1" in summary


# ── acceptance ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("window", [1, 2, 3, 4, 5])
def test_it_catches_the_failure_it_was_built_for(window: int) -> None:
    """The thesis-run briefings reported the sum of the five-row breakdown as
    the total, in every window. A check that cannot catch that is decoration.

    Uses the committed reports, so this is the real output of a real model on
    real (synthetic) data, not a constructed example.
    """
    reports = json.loads((OUTPUTS / "published" / "all_reports.json").read_text())
    record = next(r for r in reports if r["window_id"] == window)
    context = record["aggregated_context"]
    fabricated = sum(a["n"] for a in context["security_summary"]["top_attacks"])

    result = check_grounding(record["cybersum_report"], context)

    assert not result.ok, "the fabricated total was not flagged"
    assert any(abs(f.value - fabricated) < 1 for f in result.ungrounded), (
        f"expected {fabricated} to be flagged; flagged "
        f"{[f.raw for f in result.ungrounded]}"
    )


def test_the_current_reports_are_fully_grounded() -> None:
    """After the fix, every figure in every Group C briefing traces to the
    context. This is the number the README quotes, so it is asserted rather
    than remembered."""
    reports = json.loads((OUTPUTS / "runs" / "2026-08-12" / "all_reports.json").read_text())
    failures = []
    for record in reports:
        if record["group"] != "C":
            continue
        result = check_grounding(record["report"], record["aggregated_context"])
        if not result.ok:
            failures.append((record["window_id"], [f.raw for f in result.ungrounded]))
    assert not failures, f"ungrounded figures in Group C: {failures}"
