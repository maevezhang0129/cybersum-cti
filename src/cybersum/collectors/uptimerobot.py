"""
==============================================================
Module: test_fetch_api_uptimerobot.py
==============================================================

Purpose:
  - Fetches real-time monitor status (UP/DOWN) from the UptimeRobot API.
  - Ingests the full raw JSON response into the central PostgreSQL `logs` table.
  - Serves as the primary source for "Availability" monitoring in the Cybersum pipeline.

Key Function:
  - run_uptime_check(): The main entry point, designed to be called by the orchestration pipeline.

Environment Variables:
  - UPTIMEROBOT_API_TOKEN: Required for API authentication.

Usage:
  - Standalone: python src/test_fetch_api_uptimerobot.py
  - Imported:   from test_fetch_api_uptimerobot import run_uptime_check
"""



import datetime
import json
import os

import requests

# Configuration & Constants

API_TOKEN = os.getenv("UPTIMEROBOT_API_TOKEN")
URL = "https://api.uptimerobot.com/v2/getMonitors"

class UptimeRobotStatus:
    """Standard UptimeRobot API status codes

    Attributes:
        PAUSED (int): Monitor is paused (0).
        NOT_CHECKED (int): Monitor has not been checked yet (1).
        UP (int): Monitor is up and reachable (2).
        SEEMS_DOWN (int): Monitor seems down, verifying (8).
        DOWN (int): Monitor is confirmed down (9).
    """

    PAUSED = 0
    NOT_CHECKED = 1
    UP = 2
    SEEMS_DOWN = 8
    DOWN = 9

STATUS_MAP = {
    UptimeRobotStatus.UP: "UP ✅",
    UptimeRobotStatus.DOWN: "DOWN ❌",
    UptimeRobotStatus.SEEMS_DOWN: "SEEMS DOWN 🔴",
    UptimeRobotStatus.PAUSED: "PAUSED ⏸️",
    UptimeRobotStatus.NOT_CHECKED: "NOT CHECKED YET ⏳",}

def run_uptime_check():
    """
    Main entry point for the data pipeline.
    Fetches status from UptimeRobot API and inserts the result into the database.

    This function performs the following steps:
    1. Validates the presence of the API token.
    2. Sends a POST request to the UptimeRobot `getMonitors` endpoint.
    3. Handles various HTTP and network exceptions (401, 429, Timeout, etc.).
    4. Prints a human-readable status summary to stdout for logging.
    5. Ingests the raw JSON response payload into the database via `db_utils`.

    Returns:
        bool: True if the API call was successful AND data was inserted into the DB.
              False if any step (Validation, Fetch, Parse, DB Insert) fails.
    """

    # 1. Validate Environment Variable
    if not API_TOKEN:
        print("❌ Error: Missing environment variable 'UPTIMEROBOT_API_TOKEN'")
        return False


    # 2. Fetch data from UptimeRobot API
    print("Fetching data from UptimeRobot...")
    try:
        response = requests.post(URL, data={"api_key": API_TOKEN, "format": "json"}, timeout=30)
        response.raise_for_status()
        data = response.json()

    except requests.exceptions.Timeout:
        print("⏱️ API Request Timed Out (30s)")
        return False

    except requests.exceptions.HTTPError as http_err:
        status_code = response.status_code
        if status_code == 401:
            print("❌Authentication Failed: Invalid API Token. Check .env file.")
        elif status_code == 429:
            print("❌ Rate Limit Exceeded: Slow down the scheduler.")
        elif status_code >= 500:
            print(f"❌ UptimeRobot Server Error ({status_code}): Not your fault, try again later.")
        else:
            print(f"❌ HTTP Error {status_code}: {http_err}")
        return False

    except requests.exceptions.ConnectionError:
        print("🌐 Network Connection Error: Check your internet or DNS.")
        return False

    except requests.exceptions.RequestException as e:
        print(f"❌ Unexpected API Error: {e}")
        return False

    except json.JSONDecodeError:
        print("❌ Invalid JSON response from API")
        return False

    # 3. Process and Store Data
    if data and data.get('stat') == 'ok':

        raw_monitors = data.get("monitors", [])

        if isinstance(raw_monitors, list):
            monitors = raw_monitors

        else:
            print(f"⚠️ Warning: 'monitors' field is not a list (Got {type(raw_monitors)}). Treating as empty.")
            monitors = []

        if not monitors:
            print("ℹ️ No monitors found in the response.")

        # --- Part A: Console Output (For Debugging/Verification) ---
        # This loop prints a human-readable summary to the terminal.
        print("\n--- Monitor Status Summary ---")
        for monitor in monitors:
            if not isinstance(monitor, dict):
                print(f"⚠️ Skipping invalid monitor entry: {monitor}")
                continue

            name = monitor.get("friendly_name", "N/A")
            url = monitor.get("url", "N/A")
            monitor_status = monitor.get("status")

            # Map status codes to readable text
            status_text = STATUS_MAP.get(monitor_status, f"UNKNOWN ({monitor_status})")

            print(f"{name} | {url} | {status_text}")
        print("------------------------------\n")

        # --- Part B: Database Ingestion (The Core Task) ---
        print("Writing raw data to database...")

        try:
            insert_log(
                provider='uptimerobot',
                service='uptime_monitor',
                log_type='status_check',
                raw_data_dict=data,
                event_timestamp=datetime.datetime.utcnow()
            )
            print("✅ Data ingestion successful.")
            return True

        except Exception as db_err:
            print(f"❌ Database ingestion failed: {db_err}")
            return False
    else:
        print("❌ Failed to fetch valid data or API returned an error.")
        return False

# === Main Execution Block ===
# This block ensures the function runs only when the script is executed directly,
# not when it is imported by another script (like run_pipeline.py).
if __name__ == "__main__":
    result = run_uptime_check()
    print(f"\nExecution Result: {result}")

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
