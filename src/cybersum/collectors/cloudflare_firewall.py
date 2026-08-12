#!/usr/bin/env python3

"""
==============================================================
Module: test_fetch_api_cf_firewall_events.py
==============================================================

Purpose:
  - Fetches recent Firewall/WAF events (Forensics) from Cloudflare GraphQL.
  - captures detailed attack vectors (SQLi, XSS, Bot challenges).
  - Ingests detailed logs into the central PostgreSQL `logs` table.

Key Function:
  - run_firewall_forensics(): Returns True if successful, False if failed.


=== Cloudflare Firewall Actions Reference ===

allow
    Request was allowed through without challenge.
    Normal traffic, passed all security checks.

log
    Request was logged but not blocked or challenged.
    Used in “Simulate” mode rules or analytics-only WAF policies.

js_challenge
    Client was given a JavaScript challenge to prove it’s a browser.
    Common for bot-mitigation of suspicious traffic.

managed_challenge
    Advanced Cloudflare-managed challenge — visual CAPTCHA or silent human check.
    Triggered by Bot Management / WAF to verify the request is human.

challenge
    Legacy CAPTCHA challenge.
    Used by older firewall rules that still rely on “Challenge”.

block
    Request was blocked outright.
    Triggered by WAF rules (SQL injection, XSS), bot rules, or custom security policies.

simulate
    Simulated block/challenge, logged but not enforced.
    Useful for testing WAF rules before turning them live.

skip
    Request skipped due to a bypass or exemption.
    Public APIs, whitelisted IPs, rate-limit bypass rules, etc.

connection_close
    Cloudflare terminated the TCP connection immediately.
    Sometimes used in rate-limit or DDoS mitigation actions.

redirect
    User was redirected to another page/location.
    Can occur in custom WAF/page rules.

serve_error
    Cloudflare served a custom error page (e.g., 403).
    Returned when a firewall/WAF rule is configured to show an error message.
"""

import datetime
import json
import os

import requests

# === Configuration & Constants ===


API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
if not API_TOKEN:
    raise SystemExit("❌ CLOUDFLARE_API_TOKEN not set")

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

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
}
#===========================================================
# Helper Functions
#===========================================================

def get_time_range(minutes=5):
    end = datetime.datetime.utcnow()
    start = end - datetime.timedelta(minutes=minutes)
    return (
        start.isoformat(timespec="seconds") + "Z",
        end.isoformat(timespec="seconds") + "Z"
    )


def fetch_firewall_events(zone_name, zone_id, minutes=5):
    """
    Fetch firewall events from Cloudflare GraphQL API for a single zone.

    Args:
        zone_name (str): Human-readable zone name
        zone_id (str): Cloudflare Zone ID
        minutes (int): Time window in minutes

    Returns:
        list: List of firewall events (may be empty if no events)

    Raises:
        Exception: If API request fails with detailed error message
    """

    start, end = get_time_range(minutes)


    # GraphQL query payload
    payload = {
        "query": """
        query ($zoneTag: String!, $filter: FirewallEventsAdaptiveFilter_InputObject) {
          viewer {
            zones(filter: { zoneTag: $zoneTag }) {
              firewallEventsAdaptive(
                filter: $filter
                limit: 10
                orderBy: [datetime_DESC]
              ) {
                action
                datetime
                clientCountryName
                clientIP
                clientRequestHTTPHost
                ruleId
                description
              }
            }
          }
        }
        """,
        "variables": {
            "zoneTag": zone_id,
            "filter": {"datetime_geq": start, "datetime_leq": end},
        },
    }

    # Make the API request
    try:
        r = requests.post(GRAPHQL_URL, headers=HEADERS, json=payload, timeout=30)

        # Check HTTP status
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError:
            status_code = r.status_code
            if status_code == 401:
                raise Exception("🔒 Auth Failed: Invalid Cloudflare API Token")
            elif status_code == 403:
                raise Exception(f"🔒 Permission Denied: Token cannot access zone {zone_id}")
            elif status_code == 429:
                raise Exception("⏳ Rate Limit Exceeded: Too many requests")
            else:
                raise Exception(f"❌ HTTP Error {status_code}: {r.text}")

        data = r.json() if r.content else {}
    except requests.exceptions.Timeout:
        raise Exception(f"⏱️  API Timeout (30s) for zone {zone_name}")
    except requests.exceptions.RequestException as req_err:
        raise Exception(f"🌐 Network Connection Error: {req_err}")
    except json.JSONDecodeError:
        raise Exception(f"❌ Invalid JSON response from Cloudflare for {zone_name}")
    except Exception as e:
        # Re-raise if already our custom exception
        if "🔒" in str(e) or "❌" in str(e) or "⏱️" in str(e) or "🌐" in str(e) or "⏳" in str(e):
            raise
        raise Exception(f"❌ Unexpected error fetching events for {zone_name}: {e}")


    # graphql errors
    if data.get("errors"):
        error_msgs = [err.get("message", str(err)) for err in data["errors"]]
        raise Exception(f"❌ GraphQL Error for {zone_name}: {'; '.join(error_msgs)}")

    # Extract events data
    viewer = data.get("data", {}).get("viewer") if isinstance(data.get("data"), dict) else None
    if not viewer:
        raise Exception(f" No 'viewer' data for {zone_name} (Free plan or access restricted)")

    zones_data = viewer.get("zones") or []
    if not zones_data:
        raise Exception(f" No zone data returned for {zone_name}")

    zone_info = zones_data[0] if isinstance(zones_data[0], dict) else {}
    events = zone_info.get("firewallEventsAdaptive", [])

    # Summary
    print(f" 📊 Fetched {len(events)} events from {start} to {end}")

    return events

def process_and_store_events(zone_name, events):
    """
    Process and store firewall events into the database.

    Args:
        zone_name (str): Zone name for logging
        events (list): List of firewall events to insert

    Returns:
        int: Number of events successfully inserted
    """

    if not events:
        return 0

    print(f"💾 Inserting {len(events)} event(s) into database...")

    inserted = 0

    for e in events:
        try:
            print(
                f"   [{e.get('datetime','')}] {e.get('action','?'):<18} "
                f"from {e.get('clientCountryName','Unknown'):<3} "
                f"{e.get('clientIP','N/A'):<15} "
                f"→ {e.get('clientRequestHTTPHost','—')} "
                f"({e.get('description','')})"
            )
            insert_log(
                provider='cloudflare',
                service='firewall',
                log_type='security_event',
                raw_data_dict=e,
                event_timestamp=e.get('datetime')
            )
            inserted += 1

        except Exception as insert_err:
            print(f"⚠️  Failed to insert event: {insert_err}")
            continue

    return inserted

# ============================================================
# Main Pipeline Function
# ============================================================
def run_firewall_events_check(minutes=5):
    """
    Main entry point for Cloudflare Firewall Events monitoring pipeline.

    Workflow:
      1. Iterate through all configured zones
      2. Fetch firewall events for each zone via GraphQL API
      3. Insert events into PostgreSQL database
      4. Track success/failure per zone
      5. Return overall success status

    Args:
        minutes (int): Time window for analysis (default: 5 minutes)

    Returns:
        bool: True if all zones processed successfully, False otherwise

    Example:
        >>> if run_firewall_events_check(minutes=10):
        ...     print("Firewall events check passed!")
    """
    print(f"\n🚀 Starting Cloudflare Firewall Events Check (window={minutes}min)")

    all_success = True
    total_events = 0

    for zone_name, zone_id in ZONES.items():
        print(f"\n--- Analyzing Zone: {zone_name} ---")

        try:
            events = fetch_firewall_events(zone_name, zone_id, minutes)

            if not events:
                print(f"ℹ️  No events in the last {minutes} minutes")
                continue

            inserted = process_and_store_events(zone_name, events)

            total_events += inserted

            if inserted == len(events):
                print(f"✅ Successfully processed {inserted}/{len(events)} event(s)")
            else:
                print(f"⚠️  Partial success: Inserted {inserted}/{len(events)} event(s)")
                all_success = False
        except Exception as e:
            print(f"❌ Failed to process {zone_name}: {e}")
            all_success = False
            continue

    # Summary
    print(f"\n{'='*60}")
    print("📊 Firewall Events Check Summary:")
    print(f"   Zones Processed: {len(ZONES)}")
    print(f"   Total Events: {total_events}")
    print(f"   Status: {'✅ SUCCESS' if all_success else '⚠️  PARTIAL/FAIL'}")
    print(f"{'='*60}\n")

    return all_success

# ============================================================
# Main Execution Block
# ============================================================

if __name__ == "__main__":
    """
    Standalone execution mode.
    This block only runs when script is executed directly, not when imported.
    """
    print("\n=== Cloudflare Firewall Event Iterator ===")

    minutes = int(os.getenv("MINUTES", "5"))

    result = run_firewall_events_check(minutes=minutes)

    print(f"\n{'='*60}")
    print(f"Execution Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    print(f"{'='*60}\n")

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
