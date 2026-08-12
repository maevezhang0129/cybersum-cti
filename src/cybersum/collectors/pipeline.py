"""Run every collector once, in order.

Replaces the original ``src/run_pipeline.py``, which imported the collectors as
top-level modules while only putting ``src/`` on ``sys.path`` -- the collectors
lived in ``tests/``. It worked in the container layout, where they were copied
next to it, and raised ImportError from a clean checkout. They are a package
now, so the imports are ordinary relative ones.

Order matters. The uptime check runs first so that a service being down is known
before the firewall numbers are interpreted: a quiet edge in front of a dead
backend is not the same event as a quiet edge in front of a healthy one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Task:
    name: str
    run: Callable[[], object]


def tasks() -> list[Task]:
    # Imported inside the function so that a missing collector dependency does
    # not break importing this module.
    from .azure_webapp import run_azure_metrics_check
    from .cloudflare_ddos import run_ddos_module
    from .cloudflare_firewall import run_firewall_events_check
    from .uptimerobot import run_uptime_check

    return [
        Task("uptime", run_uptime_check),
        Task("ddos_analyzer", run_ddos_module),
        Task("firewall_events", run_firewall_events_check),
        Task("azure_metrics", run_azure_metrics_check),
    ]


def collect_all() -> dict[str, bool]:
    """Run each collector, carrying on past failures.

    One unreachable provider should cost that provider's signal, not the whole
    ingestion round -- the aggregation degrades a missing source to an empty
    result, and a briefing built on three sources beats no briefing at all.
    """
    results: dict[str, bool] = {}
    for task in tasks():
        try:
            task.run()
            results[task.name] = True
            logger.info("Collector %s completed.", task.name)
        except Exception as exc:
            results[task.name] = False
            logger.error("Collector %s failed: %s", task.name, exc)

    succeeded = sum(results.values())
    logger.info("Collectors: %d/%d succeeded.", succeeded, len(results))
    return results
