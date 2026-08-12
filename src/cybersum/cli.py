"""Command line entry point.

The only place in the package that reads the process environment or loads a
``.env``. Everything below takes a :class:`Settings` it was handed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .aggregation import PRODUCTION, Scope, aggregate
from .config import Settings
from .llm_client import DEFAULT_CASSETTE, make_client, model_name
from .pipeline import run_daily_report
from .prompts import load_prompt
from .storage import connect


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _load_settings(args: argparse.Namespace) -> Settings:
    env_file = Path(getattr(args, "env_file", ".env"))
    if env_file.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        except ImportError:
            pass
    return Settings.from_env(os.environ)


def _ground_truth_panel(context: dict[str, Any]) -> str:
    """The handful of facts a reader should check the briefing against."""
    security = context.get("security_summary", {})
    infra = context.get("infrastructure_health", {})
    ddos = security.get("ddos_status") or {}
    abnormal = infra.get("abnormal_services") or []
    top = (security.get("top_attacks") or [{}])[0]

    lines = [
        f"  total blocked events   {security.get('total_blocked_events', 0):,}",
        f"  top origin             {top.get('attacker_country', 'n/a')} "
        f"({top.get('block_count', 0):,} against {top.get('target_host', 'n/a')})",
        f"  DDoS health            {ddos.get('health_status', 'n/a')} "
        f"(risk {ddos.get('risk_score', 'n/a')}, "
        f"{ddos.get('malicious_percent', 'n/a')}% malicious)",
        f"  services not up        {len(abnormal)}",
    ]
    for service in abnormal:
        lines.append(f"      - {service.get('service_name')}: {service.get('status_text')}")
    return "\n".join(lines)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_daily_report(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    scope = Scope.window(args.window) if args.window else PRODUCTION

    context: dict[str, Any] | None = None
    if args.show_ground_truth and not settings.use_mock_data:
        with connect(settings.db) as conn:
            context = aggregate(conn, scope)

    result = run_daily_report(settings, scope=scope)

    if not result.ok:
        print(f"\n  Run failed at stage '{result.stage}': {result.reason}\n", file=sys.stderr)
        return 1

    if settings.llm.provider == "replay":
        print("\n  [replay] served from a recorded response; no API call was made.")
        print("  Set OPENAI_API_KEY to generate a fresh briefing.\n")

    print("=" * 78)
    print(result.report)
    print("=" * 78)

    if context is not None:
        print("\nGround truth from the aggregation the model was given:")
        print(_ground_truth_panel(context))
        print("\nCompare the figures above against the briefing. Any number in the")
        print("prose that is not derivable from this panel was not grounded in data.")

    print(f"\n{result.summary()}")
    return 0


def cmd_aggregate(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    scope = Scope.window(args.window) if args.window else PRODUCTION
    with connect(settings.db) as conn:
        context = aggregate(conn, scope)
    print(json.dumps(context, indent=2, default=str))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Capture one real completion for the offline demo to replay."""
    settings = _load_settings(args)
    if settings.llm.provider == "replay":
        print("Cannot record without credentials. Set OPENAI_API_KEY.", file=sys.stderr)
        return 1

    scope = Scope.window(args.window) if args.window else PRODUCTION
    with connect(settings.db) as conn:
        context = aggregate(conn, scope)

    prompt = load_prompt("production_report_v1")
    client = make_client(settings.llm)
    response = client.chat.completions.create(
        model=model_name(settings.llm),
        messages=[
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": json.dumps(context, default=str)},
        ],
        temperature=settings.llm.temperature,
    )

    destination = Path(args.out or DEFAULT_CASSETTE)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(UTC).date().isoformat(),
                "model": response.model,
                "prompt_name": prompt.name,
                "prompt_sha256": prompt.sha256,
                "scope": f"window {args.window}" if args.window else "production",
                "temperature": settings.llm.temperature,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "content": response.choices[0].message.content,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Recorded {response.usage.total_tokens} tokens to {destination}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    from evaluation.synthetic_data import seed_windows

    settings = _load_settings(args)
    with connect(settings.db) as conn:
        written = seed_windows(
            conn,
            windows=args.windows,
            trend_days=args.trend_days,
            seed=args.seed,
        )
    total = sum(written.values())
    print(f"Seeded {len(written)} window(s), {total:,} rows.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cybersum", description=__doc__)
    parser.add_argument("--env-file", default=".env", help="defaults to .env")
    parser.add_argument("--debug", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("daily-report", help="generate a briefing")
    report.add_argument("--window", type=int, help="use a synthetic scenario window")
    report.add_argument(
        "--show-ground-truth", action="store_true",
        help="print the aggregated facts beside the briefing",
    )
    report.set_defaults(func=cmd_daily_report)

    agg = sub.add_parser("aggregate", help="print the aggregated context as JSON")
    agg.add_argument("--window", type=int)
    agg.set_defaults(func=cmd_aggregate)

    rec = sub.add_parser("record", help="record a completion for offline replay")
    rec.add_argument("--window", type=int, default=4)
    rec.add_argument("--out", help=f"defaults to {DEFAULT_CASSETTE}")
    rec.set_defaults(func=cmd_record)

    seed = sub.add_parser("seed", help="write synthetic scenario windows")
    seed.add_argument("--windows", type=int, nargs="*", help="defaults to all five")
    seed.add_argument("--trend-days", type=int, default=90)
    seed.add_argument("--seed", type=int, default=42)
    seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.debug)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
