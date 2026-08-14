# The consumption layer

The daily briefing was not the deliverable. A **Power BI report** was — a page
people opened, showing the latest briefing, its status classification and its
origin breakdown, refreshed once a day.

That report lived in a cloud tenant this repository has no access to, and it
carried the host organisation's data and branding. It is not here and cannot be.

What is here is the contract it consumed, and a renderer that exercises it:

```bash
make demo        # seed a scenario window and generate a briefing
make dashboard   # http://localhost:8000
```

> The renderer is not a reproduction of the Power BI report and does not claim to
> be one. It reads the same endpoint, over synthetic data, so that the shape of
> the delivered thing is visible rather than asserted.

---

## The endpoint

[`adapters/azure_function/function_app.py`](../adapters/azure_function/function_app.py)
exposes one authenticated route beside the timer trigger:

```python
@app.route(route="get_latest_report", auth_level=func.AuthLevel.FUNCTION)
def get_latest_report(req: func.HttpRequest) -> func.HttpResponse:
    with connect(settings.db) as conn:
        rows = fetch_latest_report(conn)
    return func.HttpResponse(json.dumps(rows, default=str), ...)
```

`AuthLevel.FUNCTION` means the caller presents a function key; Power BI carried
it as a query parameter in the dataset's credentials, not in the report file.

The payload is a one-element array — seven columns, no computation:

```json
[
  {
    "report_date": "2026-08-14",
    "report_content": "1: EXECUTIVE SUMMARY\n...\n\n2: TECHNICAL BRIEF\n- ...",
    "status_code": "STATUS C",
    "top_5_origins": {"United States": 414, "China": 215},
    "total_tokens": 3683,
    "model_version": "gpt-4o",
    "created_at": "2026-08-14 08:00:12.481+00:00"
  }
]
```

`make dashboard` serves that same array at `/api/latest`, from
[`fetch_latest_report`](../src/cybersum/storage.py) — the function the Azure route
calls, not a second query written to resemble it. A test asserts the renderer's
fields and that SELECT list stay in step.

## Why the endpoint returns rows and not a report

`daily_security_reports` also carries a view,
[`vw_latest_security_briefings`](../deploy/sql/001_schema.sql), whose columns are
already presentation names:

```sql
SELECT report_date    AS "Report Date",
       report_content AS "Executive Security Briefing",
       status_code    AS "Status",
       ...
```

Two consumption paths, deliberately: the view for a direct database connection,
the endpoint for anything that only speaks HTTP. Both hand over stored columns
and neither computes anything.

That is the point. **Every number the reader sees was computed in SQL before the
model ever ran**, so the presentation layer cannot introduce a figure that the
grounding check never saw. A dashboard that derives its own totals is another
place for the failure in [findings.md](findings.md) to happen — a breakdown next
to a missing aggregate, arithmetic filling the gap.

## Connecting Power BI

Power Query M, against the endpoint:

```m
let
    Base    = "https://<your-function-app>.azurewebsites.net/api/get_latest_report",
    Raw     = Web.Contents(Base, [Query = [code = FunctionKey]]),
    Rows    = Json.Document(Raw),
    Table   = Table.FromRecords(Rows),
    Typed   = Table.TransformColumnTypes(Table, {
                  {"report_date", type date},
                  {"created_at",  type datetimezone},
                  {"total_tokens", Int64.Type}
              })
in
    Typed
```

`top_5_origins` arrives as a record and expands to a two-column table with
`Record.ToTable`.

Refresh is scheduled daily, a little after the 08:00 UTC timer trigger. Anything
more frequent re-reads a row that changes once a day; the upstream cadence is the
constraint, not the dashboard's.

## Why the prompt forbids Markdown

`report_content` is rendered **verbatim**. The Power BI text box parsed no
Markdown and did no reflow; neither does `make dashboard`, which puts the
briefing inside `white-space: pre-wrap` and nothing else.

That single downstream fact reaches all the way back into the prompt.
[`production_report_v1`](../src/cybersum/prompts/production_report_v1.txt) spends
a paragraph on it — no Markdown, uppercase section headings, hyphens for bullets —
and [`format_report_for_dashboard`](../src/cybersum/parsing.py) exists to repair
the cases where the model complies imperfectly.

It is also most of why the production prompt is twice the length of the
evaluation variant, and therefore why the two are not merged
([prompts.md](prompts.md)). The extra length is downstream integration, not
quality engineering — which is worth knowing before reading the prompt as though
every line of it were tuned for output quality.

## Status colours

The badge colour comes from the `STABLE / STATUS A / B / C` vocabulary the model
emits in its `###DATA_START###` block. The renderer imports the map from
[`notify.py`](../src/cybersum/notify.py) rather than restating it: that vocabulary
already spans four places nothing links, and a fifth copy would be a fifth thing
to drift. See [contracts.md](contracts.md).
