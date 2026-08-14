# Architecture

```
  external APIs          PostgreSQL              one model call        outputs
  ─────────────          ──────────              ──────────────        ───────
  edge firewall  ──┐                                                ┌─ email
  uptime monitor ──┼─► logs (JSONB)  ──►  7 SQL signals  ──►  LLM ──┤
  infra metrics  ──┘   schema-on-read      fixed context           └─ daily_security_reports
                                                                        └─► dashboard
```

Three layers, following a medallion shape: raw in, structured in the middle,
consumable at the end.

## Bronze — `logs`

Collectors write whatever the provider returned, unaltered, into a JSONB column.
No parsing, no normalisation, no schema negotiation at write time.

```sql
INSERT INTO logs (provider, service, log_type, event_timestamp, raw_data)
```

This is schema-on-read: aggregations pull fields back out with
`raw_data ->> 'clientCountryName'`. The upside is that a provider changing its
response shape cannot break ingestion, and history is preserved exactly as
received. The cost is that **the two sides share no schema object** — adding a
metric means agreeing on a JSON key in the collector and in the query, with
nothing to catch a disagreement. That is a real weakness and worth knowing before
adding a source.

Collectors live in [`src/cybersum/collectors/`](../src/cybersum/collectors). They
are the only components needing credentials beyond a model key, and nothing in
the demo or the evaluation runs them.

## Silver — the signals

[`aggregation.py`](../src/cybersum/aggregation.py). Never materialised as a
table; assembled in memory per run. Five of these produced the thesis numbers and
are frozen in [`tests/golden/sql/`](../tests/golden/sql); the two aggregates at
the top were added afterwards, for the reason in [findings.md](findings.md).

| Signal | Answers |
|---|---|
| `get_total_blocked` | how much was blocked, in total |
| `get_origin_countries` | how that total splits by origin country |
| `get_firewall_stats` | which five host/country pairs saw the most |
| `get_uptime_stats` | which services are not up |
| `get_azure_stats` | hourly memory average and CPU peak |
| `get_ddos_status` | the latest risk score and malicious-traffic ratio |
| `get_90day_trend` | daily volume and dominant action over 90 days |

Two properties matter more than the individual queries.

**Context size is decoupled from log volume.** The signal set is fixed, so ten
thousand rows and ten million produce the same-sized prompt. Cost and latency
stay flat as ingestion grows — which a raw-sample approach cannot offer, since a
sample large enough to stay representative has to grow with the table.

**The aggregate travels with its sample.** `total_blocked_events` is emitted
before `top_attacks`, and `top_attacks` carries a note saying it is a five-row
subset that does not sum to the total. That ordering is a fix for an observed
failure, not decoration — see [findings.md](findings.md).

Both scopes — the live 24-hour window and one synthetic evaluation window — run
through the same code, with every permitted difference declared as a field on
`Scope`. See [contracts.md](contracts.md).

## Gold — the briefing

One call to the model with the aggregated context. Out comes prose for two
audiences plus a fenced JSON block carrying `status_code` and `top_5_origins`;
the parser splits them, the prose goes to email and dashboard, the fields go to
columns.

Persisted with `ON CONFLICT (report_date) DO UPDATE`, so re-running a day
overwrites rather than accumulating. Each row carries its `execution_id`, token
counts, model version and the prompt's sha256 — enough to attribute a change in
output to a change in prompt or model afterwards.

## Module layout

| Path | |
|---|---|
| `config.py` | one settings tree; `from_env` takes the environment as an argument |
| `aggregation.py` | the five signals and `Scope` |
| `llm_client.py` | azure / openai / replay behind one factory |
| `report.py` | the single model call, retry, result assembly |
| `parsing.py` | side-channel extraction and dashboard formatting |
| `storage.py` | connection, upsert, log insert, JSON encoding |
| `notify.py` | HTML email |
| `pipeline.py` | six named stages and the orchestrator |
| `dashboard.py` | the briefing over the endpoint the Power BI report read |
| `cli.py` | the only place that reads `os.environ` or a `.env` |
| `adapters/azure_function/` | 59 lines translating between the runtime and the pipeline |

Two rules keep this testable. Configuration is passed in rather than read from
the environment, so tests hand in a dict instead of patching a global. And
`pipeline.py` takes its dependencies through a `Deps` record, so a full run can
be exercised without a database, a network, or a model.

## Runtime shape

A timer trigger at 08:00 UTC, and an authenticated HTTP endpoint a Power BI
report polled once a day. Serverless because the workload is one short run a day:
an always-on VM costs roughly USD 1,152 a year to be idle for all but a few
seconds of it.

Neither the report nor the tenant it ran in is in this repository. The endpoint,
the view it read, and a renderer that consumes the same payload are —
[dashboard.md](dashboard.md).

The pipeline **never raises**. The Functions runtime retries a failed
invocation, so an expired key or a malformed row would otherwise become repeated
paid model calls. A bad run returns a failed `PipelineResult` and logs which
stage stopped it.

## What is deliberately absent

- **No agent loop, no multi-model critique.** The premise is that structuring the
  input does more than orchestrating the model. The evaluation is the test of
  that premise, and a multi-agent version would be a different system.
- **No vector store or retrieval.** The relevant data is a bounded set of
  aggregates over one day, which SQL answers exactly. Approximate retrieval would
  add a failure mode to solve a problem that is not present.
- **No streaming.** The output is one document a day read by people, not a chat.
