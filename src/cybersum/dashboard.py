"""A minimal renderer for the briefing, over the endpoint the dashboard polled.

The deployed consumption layer was a Power BI report reading the authenticated
HTTP endpoint in ``adapters/azure_function``. That report lived in a cloud tenant
this repository has no access to, and it carried the host organisation's data and
branding, so it is not here and cannot be.

What is here is the *contract* it consumed, exercised. ``/api/latest`` calls
:func:`cybersum.storage.fetch_latest_report` — the same function the Azure route
calls, not a second query written to look like it — and ``/`` is a page that
consumes that JSON. Anything a reader sees on the page, Power BI could have read
from the same payload.

Two details are deliberate rather than decorative:

* The briefing renders inside ``white-space: pre-wrap`` and nothing parses
  Markdown. That is the actual downstream constraint, and it is why the
  production prompt spends a paragraph forbidding Markdown and mandating
  uppercase headings — see ``docs/prompts.md``.
* Status colours come from :mod:`cybersum.notify` rather than being restated
  here. The ``STABLE / STATUS A / B / C`` vocabulary already spans four places
  that nothing links; adding a fifth copy of it would be adding a fifth thing to
  drift.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Settings
from .notify import DEFAULT_COLOR, STATUS_COLORS
from .storage import connect, fetch_latest_report

logger = logging.getLogger(__name__)

EMPTY_STATE = (
    "No briefing has been generated yet. Run <code>make demo</code> to seed a "
    "scenario window and generate one."
)


def latest(settings: Settings) -> list[dict[str, Any]]:
    """The payload both the page and ``/api/latest`` are built from."""
    with connect(settings.db) as conn:
        return fetch_latest_report(conn)


def _as_mapping(origins: Any) -> dict[str, Any]:
    """``top_5_origins`` arrives as JSONB, but tolerate a string or a null."""
    if isinstance(origins, str):
        try:
            origins = json.loads(origins)
        except json.JSONDecodeError:
            return {}
    return origins if isinstance(origins, dict) else {}


def _origins_table(origins: Any) -> str:
    rows = _as_mapping(origins)
    if not rows:
        return '<p class="muted">No origin breakdown in this briefing.</p>'

    def sort_key(item: tuple[str, Any]) -> float:
        return float(item[1]) if isinstance(item[1], int | float) else 0.0

    cells = []
    for country, count in sorted(rows.items(), key=sort_key, reverse=True):
        shown = f"{count:,}" if isinstance(count, int) else str(count)
        cells.append(
            f"<tr><td>{html.escape(str(country))}</td>"
            f'<td class="num">{html.escape(shown)}</td></tr>'
        )
    return (
        '<table class="origins"><thead><tr><th>Origin</th>'
        f"<th class=\"num\">Blocked</th></tr></thead><tbody>{''.join(cells)}"
        "</tbody></table>"
    )


def _generated_at(value: Any) -> str:
    """Minute precision. Microseconds carry no meaning for a daily job and push
    the value onto its own line in a narrow column."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC" if value.tzinfo else "%Y-%m-%d %H:%M")
    return str(value) if value else "n/a"


def _meta_row(row: dict[str, Any]) -> str:
    items = [
        ("Model", row.get("model_version") or "n/a"),
        ("Tokens", f"{row['total_tokens']:,}" if row.get("total_tokens") else "n/a"),
        ("Generated", _generated_at(row.get("created_at"))),
    ]
    return "".join(
        f'<div class="meta-item"><span class="meta-label">{html.escape(label)}</span>'
        f"<span>{html.escape(str(value))}</span></div>"
        for label, value in items
    )


def render_page(rows: list[dict[str, Any]]) -> str:
    """The whole page, self-contained: no external CSS, fonts or scripts."""
    if not rows:
        body = f'<div class="card empty">{EMPTY_STATE}</div>'
        subtitle = "no data"
    else:
        row = rows[0]
        status = str(row.get("status_code") or "UNKNOWN")
        colour = STATUS_COLORS.get(status, DEFAULT_COLOR)
        # Model-generated text going into an HTML document. Escaping it is the
        # only thing between a prompt injection and script execution, exactly as
        # in notify.render_html.
        report = html.escape(str(row.get("report_content") or ""))
        subtitle = str(row.get("report_date") or "")

        body = f"""
    <div class="layout">
      <div class="card">
        <div class="status-row">
          <span class="badge" style="background:{colour}">{html.escape(status)}</span>
          <span class="muted">Report date {html.escape(subtitle)}</span>
        </div>
        <pre class="briefing">{report}</pre>
      </div>
      <aside>
        <div class="card">
          <h2>Top origins</h2>
          {_origins_table(row.get("top_5_origins"))}
        </div>
        <div class="card">
          <h2>Run</h2>
          <div class="meta">{_meta_row(row)}</div>
        </div>
      </aside>
    </div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cybersum — daily briefing</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f4f6f8; color: #23282d;
         font: 15px/1.6 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }}
  header {{ background: #0056b3; color: #fff; padding: 18px 24px; }}
  header h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
  header p {{ margin: 4px 0 0; font-size: 12px; opacity: .8; }}
  main {{ max-width: 1160px; margin: 24px auto; padding: 0 16px; }}
  .card {{ background: #fff; border: 1px solid #e3e7eb; border-radius: 8px;
           padding: 20px; margin-bottom: 16px; }}
  /* Landscape, like the report this stands in for: the briefing takes the
     column, the structured fields sit beside it rather than below. */
  .layout {{ display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr) 300px;
             align-items: start; }}
  aside .card:last-child {{ margin-bottom: 0; }}
  @media (max-width: 820px) {{ .layout {{ grid-template-columns: 1fr; }} }}
  h2 {{ margin: 0 0 12px; font-size: 13px; text-transform: uppercase;
        letter-spacing: .06em; color: #6b7480; }}
  .status-row {{ display: flex; align-items: center; gap: 12px;
                 margin-bottom: 16px; }}
  .badge {{ color: #fff; font-weight: 700; font-size: 12px; letter-spacing: .05em;
            padding: 5px 12px; border-radius: 4px; }}
  .muted {{ color: #6b7480; font-size: 13px; }}
  /* The briefing renders verbatim. Nothing here parses Markdown, which is the
     downstream fact the production prompt is written against. */
  .briefing {{ white-space: pre-wrap; word-wrap: break-word; margin: 0;
               font: 14px/1.65 "SF Mono", Menlo, Consolas, monospace;
               color: #1c1f23; }}
  table.origins {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  table.origins th {{ text-align: left; font-size: 12px; color: #6b7480;
                      font-weight: 600; padding-bottom: 6px;
                      border-bottom: 1px solid #e3e7eb; }}
  table.origins td {{ padding: 7px 0; border-bottom: 1px solid #f0f2f4; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .meta-item {{ display: flex; justify-content: space-between; gap: 12px;
                padding: 7px 0; border-bottom: 1px solid #f0f2f4; font-size: 14px; }}
  .meta-label {{ color: #6b7480; white-space: nowrap; }}
  .meta-item span + span {{ text-align: right; }}
  .empty {{ color: #6b7480; }}
  footer {{ max-width: 940px; margin: 0 auto 32px; padding: 0 16px;
            font-size: 12px; color: #8b939c; }}
  code {{ background: #eef1f4; padding: 1px 5px; border-radius: 3px; }}
</style>
</head>
<body>
<header>
  <h1>Cybersecurity Daily Briefing</h1>
  <p>Cybersum &middot; {html.escape(subtitle)}</p>
</header>
<main>{body}</main>
<footer>
  Served from <code>/api/latest</code>, the same payload the deployed Power BI
  report read from the Azure Functions endpoint. Synthetic data.
</footer>
</body>
</html>
"""


def render_static(settings: Settings) -> str:
    """The same page the server serves, as a string. One rendering path."""
    return render_page(latest(settings))


def make_handler(settings: Settings) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "cybersum"

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: str, content_type: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        # Name fixed by BaseHTTPRequestHandler's dispatch, not a style choice.
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            try:
                rows = latest(settings)
            except Exception:
                # The exception text names the host, database and user. Log it,
                # do not return it -- same reasoning as the Azure route.
                logger.exception("Could not read the latest briefing.")
                self._send(500, '{"error": "internal error"}', "application/json")
                return

            if path == "/api/latest":
                self._send(200, json.dumps(rows, default=str), "application/json")
            elif path == "/":
                self._send(200, render_page(rows), "text/html")
            else:
                self._send(404, '{"error": "not found"}', "application/json")

    return Handler


def serve(settings: Settings, port: int = 8000) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(settings))
    print(f"  Briefing at http://localhost:{port}   JSON at /api/latest")
    print("  Ctrl-C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        httpd.server_close()
