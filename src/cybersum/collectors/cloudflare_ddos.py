#!/usr/bin/env python3

"""
==============================================================
 Cloudflare DDoS Risk Analyzer (Cybersum Module - Full Version)
==============================================================

This module computes a DDoS RISK SCORE (%) using Cloudflare's
firewallEventsAdaptive output. It is intentionally designed for:

    • Cybersum Cybersecurity Dashboard (Backend)
    • Trend Analysis
    • Real-time Risk Classification
    • Forensic Summary of Attack Behavior

The goal is to turn raw Cloudflare firewall logs into a single,
intelligible **DDoS Health Meter** that Cybersum can display.


-------------------------------------------------
📌 HOW THE DDoS RISK SCORE (%) IS CALCULATED
-------------------------------------------------

We compute four major signals:

-------------------------------------------------
1) BLOCK SURGE FACTOR (0–40 pts)
-------------------------------------------------
More BLOCK events → stronger denial-of-service characteristics.

Formula:
    block_points = min(40, (blocks / window_size) * 40)

Meaning:
    • If all events are "block", score → 40
    • If half are block, score → 20

-------------------------------------------------
2) CHALLENGE FLOOD FACTOR (0–30 pts)
-------------------------------------------------
High challenge volume = botnets, scrapers, credential stuffing,
or automated traffic bursts.

Included actions:
    • managed_challenge
    • js_challenge

Formula:
    challenge_points = min(30, (challenges / window_size) * 30)

-------------------------------------------------
3) MALICIOUS RATIO FACTOR (0–20 pts)
-------------------------------------------------
Ratio of "bad" events to total events.

    ratio = (blocks + challenges) / events

If ratio = 1.0 → all traffic malicious → +20 pts
If ratio = 0.5 → +10 pts
If ratio = 0.2 → +4 pts

-------------------------------------------------
4) BURST RATE FACTOR (0–10 pts)
-------------------------------------------------
Measures raw events-per-minute density.

Formula:
    burst_points = min(10, (events_per_min / 100) * 10)

Interpretation:
    • 100 events/min → full 10 points
    • 10 events/min → only 1 point

-------------------------------------------------
📌 FINAL DDoS RISK SCORE
-------------------------------------------------

    RISK = block_pts + challenge_pts + malratio_pts + burst_pts

Range:
    0%  = perfectly safe
    100% = severe L7 DDoS attack

-------------------------------------------------
📌 HEALTH METER CATEGORIES
-------------------------------------------------

SAFE (0–30%)
    Minimal risk, normal firewall noise.

ELEVATED (31–60%)
    Increased scanning, small botnet probing, credential sprays.

WARNING (61–80%)
    High-intensity botnets, coordinated L7 probing, possible API pressure.

CRITICAL (81–100%)
    Active L7 DDoS attack likely underway.
    Backend/API resources can become affected.


-------------------------------------------------
📌 OUTPUT FORMAT (for Cybersum dashboard)
-------------------------------------------------

{
  "events": 100,
  "blocks": 24,
  "challenges": 76,
  "others": 0,
  "events_per_min": 20.0,
  "malicious_ratio": 1.0,
  "risk_score": 88.0,
  "health": "CRITICAL"
}

-------------------------------------------------
This is the full version, ready for integration.
"""

import datetime
import json
import os

import requests

GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"

API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
if not API_TOKEN:
    raise SystemExit("❌ CLOUDFLARE_API_TOKEN not set")

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
}

# Your zones
def _load_zones() -> dict:
    """Zone name -> Cloudflare zone ID, from CLOUDFLARE_ZONES.

    Format: "example.org=<zone-id>,other.org=<zone-id>". Empty by default, so a
    clean checkout collects nothing rather than pointing at someone's zones.
    """
    zones = {}
    for pair in os.environ.get("CLOUDFLARE_ZONES", "").split(","):
        name, _, zone_id = pair.partition("=")
        if name.strip() and zone_id.strip():
            zones[name.strip()] = zone_id.strip()
    return zones


ZONES = _load_zones()


# ---------------------------------------------------------
# TIME RANGE HELPERS
# ---------------------------------------------------------
def time_range(minutes=5):
    """Generates ISO 8601 timestamps for the API query."""
    end = datetime.datetime.utcnow()
    start = end - datetime.timedelta(minutes=minutes)
    return (
        start.isoformat(timespec="seconds") + "Z",
        end.isoformat(timespec="seconds") + "Z",
    )


# ---------------------------------------------------------
# API CALL — firewallEventsAdaptive (EU-SAFE)
# ---------------------------------------------------------
def fetch_firewall_events(zone_id, minutes=5):
    """Fetches raw firewall events."""

    start, end = time_range(minutes)

    query = """
    query ($zoneTag: String!, $filter: FirewallEventsAdaptiveFilter_InputObject) {
      viewer {
        zones(filter: {zoneTag: $zoneTag}) {
          firewallEventsAdaptive(
            limit: 500
            orderBy: [datetime_DESC]
            filter: $filter
          ) {
            action
            datetime
            clientIP
            clientCountryName
            ruleId
            description
          }
        }
      }
    }
    """

    payload = {
        "query": query,
        "variables": {
            "zoneTag": zone_id,
            "filter": {
                "datetime_geq": start,
                "datetime_leq": end
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(GRAPHQL, headers=headers, json=payload, timeout=25)

        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError:
            status_code = r.status_code
            if status_code == 401:
                raise Exception("🔒 Auth Failed: Invalid Cloudflare Token.")
            elif status_code == 403:
                raise Exception(f"🔒 Permission Denied: Token cannot access zone {zone_id}.")
            elif status_code == 429:
                raise Exception("⏳ Rate Limit Exceeded: Slow down requests.")
            else:
                raise Exception(f"❌ HTTP Error {status_code}: {r.text}")

        data = r.json()

    except requests.exceptions.Timeout:
        raise Exception(f"⏱️  API Timeout (25s) for zone {zone_id}")
    except requests.exceptions.RequestException as req_err:
        raise Exception(f"🌐 Network Connection Error: {req_err}")
    except json.JSONDecodeError:
        raise Exception("❌ Invalid JSON response from Cloudflare")

    # GraphQL logic errors
    if data.get("errors"):
        error_msgs = [e.get("message", "Unknown error") for e in data["errors"]]
        raise Exception(f"❌ GraphQL Logic Error: {'; '.join(error_msgs)}")

    if not data.get("data"):
        raise Exception("❌ Empty 'data' field in response.")

    return (
        data.get("data", {})
            .get("viewer", {})
            .get("zones", [{}])[0]
            .get("firewallEventsAdaptive", []) or []
    )


# ---------------------------------------------------------
# DDoS RISK ENGINE
# ---------------------------------------------------------
def compute_ddos_risk(events, window_minutes=5):
    total = len(events)

    if total == 0:
        return {
            "events": 0,
            "blocks": 0,
            "challenges": 0,
            "others": 0,
            "events_per_min": 0.0,
            "malicious_ratio": 0.0,
            "risk_score": 0.0,
            "health": "SAFE",
        }

    blocks = sum(1 for e in events if e.get("action") == "block")
    challenges = sum(
        1 for e in events if e.get("action") in ("managed_challenge", "js_challenge")
    )
    others = total - blocks - challenges

    events_per_min = total / window_minutes
    malicious_ratio = (blocks + challenges) / total

    block_pts = min(40, (blocks / total) * 40)
    challenge_pts = min(30, (challenges / total) * 30)
    malratio_pts = malicious_ratio * 20
    burst_pts = min(10, (events_per_min / 100) * 10)

    risk = block_pts + challenge_pts + malratio_pts + burst_pts

    if risk < 30:
        health = "SAFE"
    elif risk < 60:
        health = "ELEVATED"
    elif risk < 80:
        health = "WARNING"
    else:
        health = "CRITICAL"

    return {
        "events": total,
        "blocks": blocks,
        "challenges": challenges,
        "others": others,
        "events_per_min": round(events_per_min, 2),
        "malicious_ratio": round(malicious_ratio, 2),
        "risk_score": round(risk, 1),
        "health": health,
    }


# ---------------------------------------------------------
# RUN for all zones
# ---------------------------------------------------------

def run_ddos_module(minutes=5):
    """
    Main entry point for Cloudflare DDoS risk analysis pipeline.

    Analyzes all configured zones and inserts risk scores into database.

    Args:
        minutes (int): Time window for analysis (default: 5 minutes)

    Returns:
        bool: True if all zones processed successfully, False otherwise

    """

    print(f"\n🚀 Starting Cloudflare DDoS Analysis (window={minutes}min)")

    all_success = True


    for name, zid in ZONES.items():
        print(f"--- Analyzing Zone: {name} ---")

        try:
            # 1. Fetch data
            events = fetch_firewall_events(zid, minutes=minutes)

            if events is None:
                raise Exception("API fetch returned None")

            # 2. Compute risk
            risk_data = compute_ddos_risk(events, window_minutes=minutes)

            # 3. Add zone name to the data (optional but useful)
            risk_data['zone_name'] = name
            print(f"   Risk: {risk_data['risk_score']}% ({risk_data['health']}) | Events: {risk_data['events']}")


            # 4. Insert into Database
            print(f"Inserting DDoS data for {name}...")
            insert_log(
                provider='cloudflare',
                service='ddos_analyzer',
                log_type='risk_score',
                raw_data_dict=risk_data
            )
            print("   ✅ Success")

        except Exception as e:
            print(f"❌ Failed to process {name}: {e}")
            all_success = False
            continue

    if all_success:
        print("\n✅ DDoS Monitor completed successfully.")
        return True
    else:
        print("\n⚠️ DDoS Monitor completed with errors.")
        return False


# ---------------------------------------------------------
# Standalone test
# ---------------------------------------------------------
if __name__ == "__main__":
    """
    Standalone execution mode.
    This block only runs when script is executed directly, not when imported.
    """
    print("\n=== Cloudflare DDoS Analyzer — LIVE RUN ===")

    result = run_ddos_module()

    print(f"\n{'='*50}")
    print(f"Execution Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    print(f"{'='*50}\n")

    # Exit with appropriate code for shell scripts integration
    exit(0 if result else 1)

# ── database access ──────────────────────────────────────────────────────────
# Collectors are the only place that writes to the Bronze layer. They open their
# own connection rather than being handed one, because each runs as a standalone
# cron-style job.

def insert_log(provider, service, log_type, raw_data_dict, event_timestamp=None):
    import os

    from ..config import DatabaseSettings
    from ..storage import connect
    from ..storage import insert_log as _insert
    with connect(DatabaseSettings.from_env(os.environ)) as conn:
        _insert(
            conn,
            provider=provider,
            service=service,
            log_type=log_type,
            raw_data=raw_data_dict,
            event_timestamp=event_timestamp,
        )
