"""Run the three-group experiment.

    python -m evaluation.cli run --out evaluation/outputs/runs/$(date +%s)

Makes roughly 60 model calls (15 briefings plus 45 judging passes) and costs a
few dollars, so it is never wired into a default target.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from cybersum.config import Settings
from cybersum.llm_client import make_client
from cybersum.storage import connect

from .harness import GROUPS, Briefing, run_group, score_briefing, summarise, write_outputs
from .synthetic_data import WINDOW_PROFILES


def cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env(os.environ)
    if settings.llm.provider == "replay":
        print("This needs a live model. Set OPENAI_API_KEY.", file=sys.stderr)
        return 1

    windows = args.windows or sorted(WINDOW_PROFILES)
    groups = [g for g in GROUPS if g.key in (args.groups or [g.key for g in GROUPS])]
    calls = len(windows) * len(groups) * (1 + args.judge_runs)
    print(f"{len(windows)} window(s) x {len(groups)} group(s) ~= {calls} model calls.")
    if not args.yes:
        print("Re-run with --yes to proceed.", file=sys.stderr)
        return 1

    client = make_client(settings.llm)
    briefings: list[Briefing] = []
    scores: dict[tuple[int, str], dict] = {}

    with connect(settings.db) as conn:
        for window_id in windows:
            scenario = WINDOW_PROFILES[window_id].label
            for group in groups:
                briefing = run_group(conn, client, settings.llm, group, window_id, scenario)
                briefings.append(briefing)
                scores[(window_id, group.key)] = score_briefing(
                    client, settings.llm, briefing, runs=args.judge_runs
                )
                score = scores[(window_id, group.key)]
                print(
                    f"  window {window_id} group {group.key}: "
                    f"avg {score['avg_score']:.2f} "
                    f"(fact {score['factual_accuracy']:.2f}, "
                    f"compl {score['completeness']:.2f}, "
                    f"sitaw {score['situational_awareness']:.2f})"
                )

    destination = Path(args.out)
    write_outputs(destination, briefings, scores)

    with (destination / "FINAL_three_group_results.csv").open() as handle:
        print(summarise(list(csv.DictReader(handle))))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Reprint a results table. No model calls."""
    path = Path(args.results) / "FINAL_three_group_results.csv"
    if not path.is_file():
        print(f"No results at {path}", file=sys.stderr)
        return 1
    with path.open() as handle:
        rows = list(csv.DictReader(handle))

    print(f"\n{path}\n")
    print(f"{'win':>4} {'scenario':<10} {'group':<7} {'fact':>5} {'compl':>6} "
          f"{'sitaw':>6} {'avg':>5}")
    for row in rows:
        print(f"{row['window_id']:>4} {row['scenario']:<10} {row['group']:<7} "
              f"{float(row['factual_accuracy']):>5.2f} "
              f"{float(row['completeness']):>6.2f} "
              f"{float(row['situational_awareness']):>6.2f} "
              f"{float(row['avg_score']):>5.2f}")
    print(summarise(rows))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Two result sets side by side. No model calls."""
    def load(directory: str) -> dict[tuple[str, str], float]:
        with (Path(directory) / "FINAL_three_group_results.csv").open() as handle:
            return {
                (r["window_id"], r["group"]): float(r["avg_score"])
                for r in csv.DictReader(handle)
            }

    left, right = load(args.baseline), load(args.candidate)
    print(f"\n{'win':>4} {'group':<8} {'baseline':>9} {'candidate':>10} {'delta':>7}")
    for key in sorted(left.keys() & right.keys()):
        delta = right[key] - left[key]
        print(f"{key[0]:>4} {key[1]:<8} {left[key]:>9.2f} {right[key]:>10.2f} {delta:>+7.2f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evaluation", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="generate and score (costs money)")
    run.add_argument("--windows", type=int, nargs="*")
    run.add_argument("--groups", nargs="*", choices=["A", "B", "C"])
    run.add_argument("--judge-runs", type=int, default=3)
    run.add_argument("--out", default="evaluation/outputs/runs/latest")
    run.add_argument("--yes", action="store_true", help="confirm the spend")
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("report", help="reprint a results table")
    report.add_argument("--results", default="evaluation/outputs/published")
    report.set_defaults(func=cmd_report)

    compare = sub.add_parser("compare", help="two result sets side by side")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    compare.set_defaults(func=cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s", stream=sys.stderr)
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
