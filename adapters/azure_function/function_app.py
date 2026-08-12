"""Azure Functions entry point.

Deliberately thin. Everything it does beyond translating between the Functions
runtime and the pipeline belongs in ``cybersum`` proper, where it can be tested
without a cloud subscription. This file was 381 lines and held the entire daily
run in one try block.

The Functions runtime injects application settings into the process
environment, so ``Settings.from_env(os.environ)`` covers both local
``local.settings.json`` and deployed configuration with no file reads.
"""

from __future__ import annotations

import json
import logging
import os

import azure.functions as func

from cybersum.config import Settings
from cybersum.pipeline import run_daily_report
from cybersum.storage import connect, fetch_latest_report

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 0 8 * * *", arg_name="timer", run_on_startup=False, use_monitor=False
)
def CybersumDailyReport(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logging.warning("Timer is past due; running anyway.")

    result = run_daily_report(Settings.from_env(os.environ))

    # Never re-raise. The runtime retries failed invocations, which would turn a
    # bad input or an expired key into repeated paid model calls.
    (logging.info if result.ok else logging.error)(result.summary())


@app.route(route="get_latest_report", auth_level=func.AuthLevel.FUNCTION)
def get_latest_report(req: func.HttpRequest) -> func.HttpResponse:
    settings = Settings.from_env(os.environ)
    try:
        with connect(settings.db) as conn:
            rows = fetch_latest_report(conn)
        return func.HttpResponse(
            json.dumps(rows, default=str), mimetype="application/json", status_code=200
        )
    except Exception:
        # The exception text can name the host, database and user. Log it, do
        # not return it.
        logging.exception("Failed to read the latest briefing.")
        return func.HttpResponse(
            json.dumps({"error": "internal error"}),
            mimetype="application/json",
            status_code=500,
        )
