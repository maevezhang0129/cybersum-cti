"""Quality thresholds, checked on every push.

A full evaluation costs money and takes twenty minutes, so CI cannot run one.
What CI can do is refuse to accept a change that would have made the committed
results worse — the reference run is a fixture, and these assert the properties
worth defending.

This is a regression gate, not a measurement. It catches "someone changed the
aggregation and the reference briefings no longer trace", not "the model got
worse today". The latter needs `make eval`, which is deliberately manual.
"""

from __future__ import annotations

import csv
import json
import pathlib
from collections import defaultdict

import pytest

REFERENCE = pathlib.Path(__file__).resolve().parents[2] / "evaluation" / "outputs" / "runs"
CURRENT = REFERENCE / "2026-08-12"

DIMENSIONS = ("factual_accuracy", "completeness", "situational_awareness")

#: Floors, not targets. Set below the reference so ordinary judge variance does
#: not fail a build, but above the thesis run so a regression to it would.
MIN_GROUP_C_OVERALL = 4.5
MIN_GROUP_C_FACTUAL = 4.5
MIN_AGGREGATION_EFFECT = 2.5  # B -> C


def group_means() -> dict[str, dict[str, float]]:
    rows = list(csv.DictReader((CURRENT / "FINAL_three_group_results.csv").open()))
    collected: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        for key in (*DIMENSIONS, "avg_score"):
            collected[row["group"]][key].append(float(row[key]))
    return {
        group: {key: sum(values) / len(values) for key, values in metrics.items()}
        for group, metrics in collected.items()
    }


def test_the_reference_run_is_present() -> None:
    """These thresholds are meaningless without it, and a missing file would
    otherwise make every test below skip quietly."""
    assert (CURRENT / "FINAL_three_group_results.csv").is_file()
    assert (CURRENT / "all_reports.json").is_file()


def test_group_c_overall_stays_above_the_floor() -> None:
    assert group_means()["GroupC"]["avg_score"] >= MIN_GROUP_C_OVERALL


def test_group_c_factual_accuracy_stays_above_the_floor() -> None:
    """The dimension the grounding work moved. A drop here means the aggregate
    stopped reaching the model."""
    assert group_means()["GroupC"]["factual_accuracy"] >= MIN_GROUP_C_FACTUAL


def test_the_aggregation_effect_survives() -> None:
    """B -> C is the claim the whole system rests on: same prompt, better data.
    If this collapses, the thesis result no longer holds for this code."""
    means = group_means()
    effect = means["GroupC"]["avg_score"] - means["GroupB"]["avg_score"]
    assert effect >= MIN_AGGREGATION_EFFECT


def test_the_prompt_effect_is_still_negative() -> None:
    """The counter-intuitive finding: the structured prompt on raw logs scores
    worse than a generic one. It has now replicated twice. If it flips, the
    README's headline needs revisiting rather than quiet correction."""
    means = group_means()
    assert means["GroupB"]["avg_score"] < means["GroupA"]["avg_score"]


def test_every_group_c_briefing_is_fully_grounded() -> None:
    """Traceability, not score. Cheap, deterministic, and the property most
    likely to break silently when the context shape changes."""
    reports = json.loads((CURRENT / "all_reports.json").read_text())
    group_c = [r for r in reports if r["group"] == "C"]

    # Without this the test passes vacuously on a results file written before
    # the harness recorded grounding -- which is exactly the drift this
    # repository already had once.
    assert group_c, "no Group C briefings in the reference run"
    missing = [r["window_id"] for r in group_c if "grounding" not in r]
    assert not missing, (
        f"windows {missing} carry no grounding record; re-run the evaluation "
        f"so the gate has something to check"
    )

    ungrounded = {
        r["window_id"]: r["grounding"]["ungrounded"]
        for r in group_c
        if r["grounding"]["ungrounded"]
    }
    assert not ungrounded, f"ungrounded figures in Group C: {ungrounded}"


@pytest.mark.parametrize("window", [1, 2, 3, 4, 5])
def test_group_c_reports_the_correct_blocked_total(window: int) -> None:
    """The original failure, asserted directly against the reference briefings
    rather than through a score. Independent of the judge entirely."""
    reports = json.loads((CURRENT / "all_reports.json").read_text())
    record = next(r for r in reports if r["group"] == "C" and r["window_id"] == window)
    total = record["aggregated_context"]["security_summary"]["total_blocked_events"]
    assert f"{total:,}" in record["report"] or str(total) in record["report"], (
        f"window {window} briefing does not state the true total {total:,}"
    )
