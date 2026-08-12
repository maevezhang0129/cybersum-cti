"""The three-group experiment and its judge.

Group A gets raw log rows and a generic prompt; group B gets raw rows and the
full prompt; group C gets the aggregated context and the full prompt. A→B moves
the prompt with the data fixed, B→C moves the data with the prompt fixed.

This merges four scripts that shared most of their code: the two report
generators and the two scorers. The rubric in particular was duplicated verbatim
across the scorers, which meant one edit away from a table comparing scores from
two different judges. It is now one file, loaded once.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cybersum.aggregation import Scope, aggregate
from cybersum.config import LLMSettings
from cybersum.grounding import check_grounding
from cybersum.llm_client import model_name
from cybersum.prompts import load_prompt
from cybersum.retry import backoff_seconds, should_retry

logger = logging.getLogger(__name__)

DIMENSIONS = ("factual_accuracy", "completeness", "situational_awareness")
JUDGE_RUNS = 3
JUDGE_TEMPERATURE = 0.3
GENERATION_TEMPERATURE = 0.3
RAW_SAMPLE_ROWS = 50


@dataclass(frozen=True)
class Group:
    key: str
    prompt: str
    uses_aggregate: bool
    description: str


GROUPS = (
    Group("A", "eval_baseline_v1", False, "raw log rows, generic prompt"),
    Group("B", "eval_cybersum_v1", False, "raw log rows, full prompt"),
    Group("C", "eval_cybersum_v1", True, "aggregated context, full prompt"),
)


@dataclass
class Briefing:
    window_id: int
    scenario: str
    group: str
    text: str
    tokens: dict[str, int]
    context: dict[str, Any] = field(default_factory=dict)


def raw_log_sample(conn: Any, window_id: int, rows: int = RAW_SAMPLE_ROWS) -> str:
    """The baseline's input: a slice of the log table, as text.

    Deliberately the most recent rows rather than a random draw, matching the
    original harness. Either way the point stands -- a fixed-size sample of a
    table dominated by routine traffic usually misses the rare critical row.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT provider, service, event_timestamp::text, raw_data::text
            FROM logs
            WHERE raw_data ->> 'window_id' = %s
            ORDER BY event_timestamp DESC
            LIMIT %s
            """,
            (str(window_id), rows),
        )
        return "\n".join("|".join(str(c) for c in row) for row in cur.fetchall())


MAX_ATTEMPTS = 8


def _call(client: Any, settings: LLMSettings, **kwargs: Any) -> Any:
    """One model call, retrying throttling and transport faults.

    A full run is around 60 calls in a few minutes, which comfortably exceeds a
    default per-minute token allowance. Without this the run dies most of the
    way through and the partial results are worthless -- the group means need
    every window.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return client.chat.completions.create(model=model_name(settings), **kwargs)
        except Exception as exc:
            if not should_retry(exc, attempt, MAX_ATTEMPTS):
                raise
            delay = backoff_seconds(attempt)
            logger.warning(
                "Attempt %d/%d failed (%s); waiting %.1fs.",
                attempt, MAX_ATTEMPTS, type(exc).__name__, delay,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


def generate(
    client: Any, settings: LLMSettings, *, system: str, user: str
) -> tuple[str, dict[str, int]]:
    response = _call(
        client,
        settings,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=GENERATION_TEMPERATURE,
    )
    usage = response.usage
    return response.choices[0].message.content or "", {
        "prompt": usage.prompt_tokens,
        "completion": usage.completion_tokens,
        "total": usage.total_tokens,
    }


def run_group(
    conn: Any, client: Any, settings: LLMSettings, group: Group, window_id: int, scenario: str
) -> Briefing:
    context = aggregate(conn, Scope.window(window_id))
    system = load_prompt(group.prompt).text
    user = (
        json.dumps(context, default=str)
        if group.uses_aggregate
        else raw_log_sample(conn, window_id)
    )
    text, tokens = generate(client, settings, system=system, user=user)
    logger.info("Window %d group %s: %d tokens.", window_id, group.key, tokens["total"])
    return Briefing(window_id, scenario, group.key, text, tokens, context)


# ── judging ──────────────────────────────────────────────────────────────────

def score_once(client: Any, settings: LLMSettings, context: dict, report: str) -> dict:
    """One judging pass.

    The judge is given the aggregated context as ground truth and no group
    label, so it cannot know which arm it is reading.
    """
    response = _call(
        client,
        settings,
        messages=[
            {"role": "system", "content": load_prompt("geval_rubric_v3").text},
            {
                "role": "user",
                "content": (
                    f"GROUND TRUTH DATA:\n{json.dumps(context, indent=2, default=str)}\n\n"
                    f"GENERATED REPORT:\n{report}\n\n"
                    f"Evaluate on all three dimensions. Respond only with the JSON object."
                ),
            },
        ],
        temperature=JUDGE_TEMPERATURE,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def score_briefing(
    client: Any, settings: LLMSettings, briefing: Briefing, runs: int = JUDGE_RUNS
) -> dict[str, Any]:
    """Judge one briefing several times and average.

    Temperature is non-zero on purpose, so the runs disagree where the rubric is
    genuinely ambiguous and the mean carries information.
    """
    passes = [score_once(client, settings, briefing.context, briefing.text) for _ in range(runs)]
    result: dict[str, Any] = {
        dim: round(sum(p[dim] for p in passes) / runs, 2) for dim in DIMENSIONS
    }
    result["avg_score"] = round(sum(result[d] for d in DIMENSIONS) / len(DIMENSIONS), 2)
    # Rationales are qualitative, so the last pass's are carried rather than merged.
    for dim in DIMENSIONS:
        result[f"{dim}_reason"] = passes[-1].get(f"{dim}_reason", "")
    result["raw_runs"] = passes
    return result


def write_outputs(
    destination: Path, briefings: list[Briefing], scores: dict[tuple[int, str], dict]
) -> None:
    import csv

    destination.mkdir(parents=True, exist_ok=True)

    with (destination / "FINAL_three_group_results.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["window_id", "scenario", "group", *DIMENSIONS, "avg_score"]
        )
        for briefing in briefings:
            score = scores[(briefing.window_id, briefing.group)]
            writer.writerow([
                briefing.window_id, briefing.scenario, f"Group{briefing.group}",
                *(score[d] for d in DIMENSIONS), score["avg_score"],
            ])

    (destination / "all_reports.json").write_text(
        json.dumps(
            [
                {
                    "window_id": b.window_id,
                    "scenario": b.scenario,
                    "group": b.group,
                    "report": b.text,
                    "tokens": b.tokens,
                    "aggregated_context": b.context,
                    "grounding": _grounding_record(b),
                    "scores": {
                        k: v for k, v in scores[(b.window_id, b.group)].items()
                        if k != "raw_runs"
                    },
                }
                for b in briefings
            ],
            indent=2,
            default=str,
        )
        + "\n"
    )
    logger.info("Wrote results to %s", destination)


def _grounding_record(briefing: Briefing) -> dict[str, Any]:
    """Traceability alongside the judge's score.

    The two measure different things and disagree usefully: a vague briefing
    traces every figure it bothers to cite. Recording both per report is what
    makes that visible instead of a footnote.
    """
    result = check_grounding(briefing.text, briefing.context)
    return {
        "checked": result.checked,
        "grounded": len(result.grounded),
        "skipped": len(result.skipped),
        "ungrounded": [{"figure": f.raw, "context": f.snippet} for f in result.ungrounded],
    }


def grounding_summary(reports: list[dict[str, Any]]) -> str:
    by_group: dict[str, list[int]] = {}
    for record in reports:
        g = record.get("grounding") or {}
        stats = by_group.setdefault(record["group"], [0, 0, 0])
        stats[0] += g.get("grounded", 0)
        stats[1] += g.get("checked", 0)
        stats[2] += len(g.get("ungrounded", []))

    lines = ["", "Grounding (figures in the prose that trace back to the context):"]
    for group in sorted(by_group):
        grounded, checked, ungrounded = by_group[group]
        share = f"{100 * grounded / checked:.0f}%" if checked else "n/a"
        lines.append(
            f"  Group {group}  {grounded}/{checked} = {share}"
            f"   ({checked / 5:.1f} figures cited per report, {ungrounded} unexplained)"
        )
    lines.append("  Note: a briefing that cites nothing scores 100%. See docs/grounding.md.")
    return "\n".join(lines)


def summarise(rows: list[dict[str, Any]]) -> str:
    """The effect decomposition, printed the way the README states it."""
    by_group: dict[str, list[float]] = {}
    for row in rows:
        by_group.setdefault(row["group"], []).append(float(row["avg_score"]))
    means = {g: sum(v) / len(v) for g, v in by_group.items()}

    lines = ["", "Group means:"]
    for group in sorted(means):
        lines.append(f"  {group}  {means[group]:.2f}")
    if {"GroupA", "GroupB", "GroupC"} <= means.keys():
        lines += [
            "",
            f"  A -> B (prompt only)      {means['GroupB'] - means['GroupA']:+.2f}",
            f"  B -> C (aggregation only) {means['GroupC'] - means['GroupB']:+.2f}",
            f"  A -> C (combined)         {means['GroupC'] - means['GroupA']:+.2f}",
        ]
    return "\n".join(lines)
