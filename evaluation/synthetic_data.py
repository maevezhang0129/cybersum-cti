"""Synthetic scenario windows.

Five windows escalating from quiet to critical, modelled on CICIDS 2018 traffic
characteristics. Every hostname and address is invented; nothing here is derived
from an operational environment.

The generator is seeded and takes an explicit base date. The original was
neither, which made the experiment unreproducible in the literal sense -- two
runs of the same script produced different data, so a re-run could not be
compared against a previous one.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

COUNTRIES = [
    "United States", "China", "Russia", "Germany",
    "Singapore", "Brazil", "Netherlands", "Canada",
]
COUNTRY_WEIGHTS = [0.45, 0.25, 0.10, 0.08, 0.05, 0.04, 0.02, 0.01]

HOSTS = ["www.site1.org", "api.site2.org", "login.site3.org", "cdn.site4.org"]
HOST_WEIGHTS = [0.5, 0.25, 0.15, 0.10]

ACTIONS = ["block", "managed_challenge", "skip", "allow"]
ACTION_WEIGHTS = [0.6, 0.25, 0.10, 0.05]

MAX_TREND_ROWS_PER_DAY = 200


@dataclass(frozen=True)
class WindowProfile:
    label: str
    n_firewall: int
    cpu_max: float
    mem_max: float
    ddos_risk: float
    malicious: float
    service_paused: bool
    ddos_health: str


WINDOW_PROFILES: dict[int, WindowProfile] = {
    1: WindowProfile("STABLE", 50, 35.0, 3500, 5.0, 0.05, False, "SAFE"),
    2: WindowProfile("STATUS_A", 200, 55.0, 5000, 15.0, 0.12, False, "SAFE"),
    3: WindowProfile("STATUS_B", 800, 78.0, 7200, 35.0, 0.28, False, "WARNING"),
    4: WindowProfile("STATUS_C", 1500, 85.0, 8500, 62.0, 0.45, True, "CRITICAL"),
    5: WindowProfile("STATUS_C", 2200, 92.0, 9800, 80.0, 0.61, True, "CRITICAL"),
}

INSERT = (
    "INSERT INTO logs (provider, service, log_type, event_timestamp, raw_data) "
    "VALUES (%s, %s, %s, %s, %s::jsonb)"
)


def _firewall_rows(
    rng: random.Random, base: datetime, profile: WindowProfile, window_id: int
) -> list[tuple]:
    rows = []
    for _ in range(profile.n_firewall):
        raw = {
            "clientRequestHTTPHost": rng.choices(HOSTS, weights=HOST_WEIGHTS)[0],
            "clientCountryName": rng.choices(COUNTRIES, weights=COUNTRY_WEIGHTS)[0],
            "action": rng.choices(ACTIONS, weights=ACTION_WEIGHTS)[0],
            "clientIP": f"198.51.100.{rng.randint(1, 254)}",
            "window_id": str(window_id),
        }
        ts = base + timedelta(minutes=rng.randint(0, 1380))
        rows.append(("cloudflare", "firewall", "event", ts, json.dumps(raw)))
    return rows


def _ddos_row(base: datetime, profile: WindowProfile, window_id: int) -> tuple:
    raw = {
        "health": profile.ddos_health,
        "risk_score": str(profile.ddos_risk),
        "malicious_ratio": str(profile.malicious),
        "window_id": str(window_id),
    }
    return (
        "cloudflare", "ddos_analyzer", "risk",
        base + timedelta(minutes=30), json.dumps(raw),
    )


def _uptime_row(base: datetime, profile: WindowProfile, window_id: int) -> tuple:
    monitors = [
        {
            "friendly_name": "Main Web Portal",
            "url": "https://www.site1.org",
            "status": 0 if profile.service_paused else 2,
        },
        {"friendly_name": "API Gateway", "url": "https://api.site2.org", "status": 2},
        {
            "friendly_name": "Login Service",
            "url": "https://login.site3.org",
            "status": 9 if window_id == 5 else 2,
        },
    ]
    raw = {"monitors": monitors, "window_id": str(window_id)}
    return (
        "uptimerobot", "uptime_check", "check",
        base + timedelta(minutes=15), json.dumps(raw),
    )


def _azure_rows(
    rng: random.Random, base: datetime, profile: WindowProfile, window_id: int
) -> list[tuple]:
    rows = []
    for hour in range(24):
        # A load peak at 18:00 gives the briefing a specific hour to name.
        load = 1.2 if hour == 18 else (0.8 if hour < 6 else 1.0)
        raw = {
            "cpu_total_sec": str(
                round(min(profile.cpu_max * load * rng.uniform(0.85, 1.0), 100.0), 2)
            ),
            "memory_mib": str(round(profile.mem_max * load * rng.uniform(0.85, 1.0), 2)),
            "window_id": str(window_id),
        }
        rows.append(
            ("azure", "backend_monitor", "metric", base + timedelta(hours=hour), json.dumps(raw))
        )
    return rows


def _trend_rows(
    rng: random.Random, base: datetime, profile: WindowProfile, window_id: int, days: int
) -> list[tuple]:
    rows = []
    for days_ago in range(1, days + 1):
        day = base - timedelta(days=days_ago)
        scale = 1.0 + (window_id * 0.1) * max(0, 1 - days_ago / 30)
        count = max(10, int(profile.n_firewall * scale * rng.uniform(0.6, 1.1)))
        for _ in range(min(count, MAX_TREND_ROWS_PER_DAY)):
            raw = {
                "clientRequestHTTPHost": rng.choice(HOSTS),
                "clientCountryName": rng.choices(COUNTRIES, weights=COUNTRY_WEIGHTS)[0],
                "action": rng.choices(ACTIONS, weights=ACTION_WEIGHTS)[0],
                "window_id": str(window_id),
            }
            ts = day + timedelta(minutes=rng.randint(0, 1380))
            rows.append(("cloudflare", "firewall", "event", ts, json.dumps(raw)))
    return rows


def seed_windows(
    conn: Any,
    *,
    windows: list[int] | None = None,
    trend_days: int = 90,
    seed: int = 42,
    base_date: datetime | None = None,
    truncate: bool = True,
) -> dict[int, int]:
    """Populate ``logs`` with one or more scenario windows.

    Returns rows written per window. ``trend_days`` is the lever that decides
    whether this takes three seconds or a minute: the full 90-day history is
    about 15,000 rows per window, which the demo does not need.
    """
    base = base_date or datetime(2026, 3, 10, tzinfo=UTC)
    targets = windows or sorted(WINDOW_PROFILES)

    written: dict[int, int] = {}
    with conn.cursor() as cur:
        if truncate:
            cur.execute("TRUNCATE TABLE logs RESTART IDENTITY;")

        for window_id in targets:
            profile = WINDOW_PROFILES[window_id]
            # Seed per window, so seeding windows 4 and 5 together produces the
            # same window 4 as seeding it alone.
            rng = random.Random(seed + window_id)
            rows = [
                *_firewall_rows(rng, base, profile, window_id),
                _ddos_row(base, profile, window_id),
                _uptime_row(base, profile, window_id),
                *_azure_rows(rng, base, profile, window_id),
                *_trend_rows(rng, base, profile, window_id, trend_days),
            ]
            cur.executemany(INSERT, rows)
            written[window_id] = len(rows)
            logger.info(
                "Window %d (%s): %d rows.", window_id, profile.label, len(rows)
            )
    conn.commit()
    return written
