#!/usr/bin/env python3

"""
==============================================================
Module: test_fetch_api_azure_webapp_metric_drupal.py
==============================================================

Purpose:
  - Fetches Azure Web App metrics (CPU & Memory) using Managed Identity.
  - Ingests metric data into the central PostgreSQL `logs` table.
  - Serves as the backend health monitoring source in the Cybersum pipeline.

Key Function:
  - run_azure_metrics_check(): Main entry point for pipeline integration.

Environment Variables:
  - METRIC_WINDOW_MINUTES: Time window for metrics (default: 60 minutes)
  - METRIC_LOG_MODE: Sampling mode - 'latest' or 'all' (default: 'latest')

Prerequisites:
  - Must run inside Azure VM with system-assigned Managed Identity
  - Managed Identity must have "Monitoring Reader" role on target Web App

Usage:
  - Standalone: python src/test_fetch_api_azure_webapp_metric_drupal.py
  - Imported: from test_fetch_api_azure_webapp_metric_drupal import run_azure_metrics_check

Examples:
  # Default (latest point, 1-hour window)
  python3 src/test_fetch_api_azure_webapp_metric_drupal.py

  # Custom window (15 minutes)
  METRIC_WINDOW_MINUTES=15 python3 src/test_fetch_api_azure_webapp_metric_drupal.py

  # Log all points in range
  METRIC_LOG_MODE=all python3 src/test_fetch_api_azure_webapp_metric_drupal.py
"""

import datetime
import json
import os

import requests

# === Configuration ===
RESOURCE = "https://management.azure.com"
SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
RG = os.environ.get("AZURE_RESOURCE_GROUP", "")
APP = os.environ.get("AZURE_WEBAPP_NAME", "")
LOG_PATH = "/var/log/azure_webapp_metrics.log"

# Adjustable time window (default 60 minutes)
INTERVAL_MINUTES = int(os.getenv("METRIC_WINDOW_MINUTES", 60))
# Adjustable sampling: 'latest' or 'all'
LOG_MODE = os.getenv("METRIC_LOG_MODE", "latest").lower()

# ============================================================

def get_token():
    """Retrieves an OAuth2 access token from the Azure Instance Metadata Service (IMDS).

    This function connects to the local link-local address available only within Azure VMs to fetch a Managed Identity token.

    Returns:
        str: The access token string.

    Raises:
        Exception: If the VM is not in Azure, Managed Identity is not configured,
                   or the request times out.
    """

    try:
        r = requests.get(
        "http://169.254.169.254/metadata/identity/oauth2/token",
        params={"api-version": "2018-02-01", "resource": RESOURCE},
        headers={"Metadata": "true"},
        timeout=10
        )
        r.raise_for_status()
        return r.json()["access_token"]

    except requests.exceptions.Timeout:
        raise Exception("⏱️  Managed Identity metadata endpoint timed out (10s)")
    except requests.exceptions.ConnectionError:
        raise Exception("🌐 Cannot reach metadata endpoint - are you running in Azure VM?")
    except requests.exceptions.HTTPError as http_err:
        status_code = r.status_code
        if status_code == 400:
            raise Exception("❌ Managed Identity not configured on this VM")
        elif status_code == 403:
            raise Exception("🔒 Managed Identity lacks required permissions")
        else:
            raise Exception(f"❌ HTTP Error {status_code}: {http_err}")
    except KeyError:
        raise Exception("❌ Invalid token response format - missing 'access_token'")
    except Exception as e:
        raise Exception(f"❌ Unexpected error acquiring token: {e}")
# ============================================================

def get_metrics(token):
    """
    Fetch CPU and Memory metrics from Azure Web App.

    Args:
        token (str): Azure Management API access token

    Returns:
        dict: Raw metrics JSON response

    Raises:
        Exception: If metrics fetch fails with detailed error message
    """

    end = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.datetime.utcnow() - datetime.timedelta(minutes=INTERVAL_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{RESOURCE}/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RG}/providers/"
           f"Microsoft.Web/sites/{APP}/providers/microsoft.insights/metrics"
           f"?metricnames=CpuTime,MemoryWorkingSet&timespan={start}/{end}"
           f"&interval=PT1M&aggregation=Average,Total&api-version=2018-01-01")

    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    except requests.exceptions.Timeout:
        raise Exception("⏱️ Azure Metrics API request timed out (30s)")
    except requests.exceptions.HTTPError as http_err:
        status_code = resp.status_code
        if status_code == 403:
            raise Exception("🔒 Managed Identity lacks 'Monitoring Reader' role on Web App")
        elif status_code == 404:
            raise Exception(f"❌ Web App not found: {RG}/{APP}")
        else:
            raise Exception(f"❌ Metrics API Error {status_code}: {http_err}")
    except Exception as e:
        raise Exception(f"❌ Unexpected error fetching metrics: {e}")

# ============================================================
def extract_entries(metrics):
    """Parses raw Azure metric JSON to extract simplified CPU and Memory data points.

    Aligns the CPU and Memory timeseries based on timestamps and formats the data
    into a clean dictionary structure.

    Args:
        metrics (dict): The raw JSON response from Azure Monitor API.

    Returns:
        list[dict]: A list of dictionaries, where each dict contains:
            - timestamp (str): ISO 8601 timestamp.
            - cpu_total_sec (float): Total CPU time in seconds.
            - memory_mib (float): Memory usage in MiB.
            Returns only the latest entry if LOG_MODE is 'latest'.
    """

    cpu_series = metrics["value"][0]["timeseries"][0]["data"]
    mem_series = metrics["value"][1]["timeseries"][0]["data"]

    entries = []
    for cpu, mem in zip(cpu_series, mem_series, strict=False):
        if "timeStamp" not in cpu or "total" not in cpu:
            continue
        entry = {
            "timestamp": cpu["timeStamp"],
            "cpu_total_sec": cpu["total"],
            "memory_mib": round(mem["total"] / (1024**2), 2)
        }
        entries.append(entry)

    if LOG_MODE == "latest":
        return [entries[-1]] if entries else []
    return entries

# ============================================================
def log_entries(entries):
    """Appends metric entries to a local log file.

    Args:
        entries (list[dict]): A list of metric dictionaries to write.
    """

    if not entries:
        return
    with open(LOG_PATH, "a") as f:
        for entry in entries:
            line = json.dumps(entry)
            f.write(line + "\n")
            print(line)

# ============================================================

def run_azure_metrics_check():
    """
    Main entry point for the Azure metrics monitoring pipeline.

    Workflow:
      1. Acquire Managed Identity token
      2. Fetch metrics from Azure Management API
      3. Parse and extract metric data points
      4. Write to local log file
      5. Insert into PostgreSQL database

    Returns:
        bool: True if successful, False otherwise

    Environment Variables:
        METRIC_WINDOW_MINUTES: Time window in minutes (default: 60)
        METRIC_LOG_MODE: 'latest' or 'all' (default: 'latest')
    """
    print(" Starting Azure Web App metrics check...")
    print(f" Config: window={INTERVAL_MINUTES}min, mode={LOG_MODE}")

    # Step 1: Acquire Managed Identity Token
    try:
        print(" Acquiring Managed Identity token...")
        token = get_token()
        print(" ✅ Token acquired successfully")

    except Exception as token_err:
        print(f"❌ Token acquisition failed: {token_err}")
        return False

    # Step 2: Fetch Metrics from Azure API
    try:
        print(f"📊 Fetching metrics for {APP}...")
        metrics = get_metrics(token)
        print("✅ Metrics fetched successfully")

    except Exception as metrics_err:
        print(f"❌ Metrics fetch failed: {metrics_err}")
        return False

    # Step 3: Parse Metric Entries
    try:
        entries = extract_entries(metrics)
        if not entries:
            print("⚠️  No valid metric entries found in response")
            return False
        print(f"📈 Parsed {len(entries)} metric entry/entries")
    except Exception as parse_err:
        print(f"❌ Failed to parse metrics: {parse_err}")
        return False

    # Step 4: Write to Local Log
    try:
        log_entries(entries)
    except Exception as log_err:
        print(f"⚠️  Warning: Failed to write local log: {log_err}")
        # Continue execution even if local log fails

    # Step 5: Insert into Database
    try:
        print(f"Writing {len(entries)} record(s) to database...")
        for entry in entries:
            insert_log(
                provider='azure',
                service='backend_monitor',
                log_type='health_metrics',
                raw_data_dict=entry,
                event_timestamp=entry.get('timestamp')
            )
        print(f"✅ Successfully inserted {len(entries)} metric entry/entries")
        print("✅ Azure metrics check completed successfully")
        print(f"  Logged {len(entries)} entry/entries from last {INTERVAL_MINUTES} min(s)")
        return True

    except Exception as db_err:
        print(f"❌ Database insertion failed: {db_err}")
        return False

# ============================================================
if __name__ == "__main__":
    """
    Standalone execution mode.
    This block only runs when script is executed directly, not when imported.
    """
    result = run_azure_metrics_check()
    print(f"\nExecution Result: {result}")

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
