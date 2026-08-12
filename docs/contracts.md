# Contracts

Three properties that hold this system together and that no tool enforces on its
own. Each spans places a refactor would not obviously connect, and each has a
test standing in for the connection.

---

## 1. The `###DATA_START###` side channel

One model call has to produce prose for people and structured fields for a
dashboard. It does so by appending a JSON object to the briefing, fenced by
literal delimiters, which the parser then strips back out.

```
1: EXECUTIVE SUMMARY
...prose...

2: TECHNICAL BRIEF
...bullets...
###DATA_START###
{"status_code": "STATUS C", "top_5_origins": {"US": 414, "CN": 96}}
###DATA_END###
```

**Four places have to agree, and nothing links them:**

| Where | What it holds |
|---|---|
| `src/cybersum/prompts/production_report_v1.txt` | the instruction to emit the block |
| `src/cybersum/parsing.py` | the regex that finds and strips it |
| `deploy/sql/001_schema.sql` | the `status_code` and `top_5_origins` columns |
| `src/cybersum/notify.py` | `status_code` in the subject line and header colour |

Change the delimiters, the field names, or the `STABLE / STATUS A / B / C`
vocabulary in one and the others break quietly.

**The failure mode.** An unparseable block does not raise. It degrades to
`status_code='STABLE'` and `top_5_origins={}` — a briefing that looks entirely
normal and reads as *all clear*, including on the day the parser breaks. The
fallback is deliberate: a briefing missing its structured block is still worth
delivering. What was not acceptable was that it be indistinguishable from a real
all-clear, so `ParsedReport.extraction_failed` now travels with it and reaches
the run summary.

**Guarded by:** the prompt lockfile (`tests/unit/test_prompt_lock.py`), four
parser failure-mode tests (`tests/unit/test_parsing.py`), and a test recording
that this instruction appears in the production prompt and no other.

**Known limitation.** If the model wraps the JSON in a markdown code fence, the
regex misses it. Pinned by `test_markdown_fenced_block_is_not_extracted` so it
cannot regress unnoticed.

---

## 2. `report_date` is unique, and that is the idempotency

The daily job writes with `ON CONFLICT (report_date) DO UPDATE`. Re-running a day
overwrites in place rather than accumulating duplicates — which matters, because
a timer trigger that retries on failure will run the same day more than once.

Without the constraint, that statement is a **runtime error**, not a silent
duplicate. The first time the job runs twice in one day, it breaks.

The original project shipped no DDL at all. `deploy/sql/001_schema.sql` was
reconstructed from the code that reads and writes these tables, which makes this
the highest-risk assumption in the repository, so it is asserted directly:

```python
def test_report_date_carries_a_unique_constraint(fresh_schema): ...
def test_rerunning_a_day_overwrites_rather_than_duplicating(db): ...
```

---

## 3. One model call, two audiences

The executive summary and the technical brief come from a **single** completion.
This is the claim the thesis tested — that one grounded call can serve a manager
and an operator without either getting the other's document.

Two changes look like improvements and are not:

- **Splitting into two calls.** Doubles cost and latency, and answers a different
  question than the one the evaluation measured.
- **Adding `response_format={"type": "json_object"}`** to make the side channel
  robust. It would also destroy the prose, which is the actual product.

`src/cybersum/report.py` therefore contains exactly one
`chat.completions.create` call site, and the count is asserted rather than
assumed:

```python
def test_both_audiences_come_from_exactly_one_model_call():
    ...
    assert len(client.calls) == 1
```

---

## 4. Divergence between production and evaluation is declared, not discovered

The aggregation runs in two modes: a rolling 24-hour window over live data, and
one synthetic scenario window. They were once two copies of the same queries,
kept separate on purpose so that "the experiment reproduces production" stayed
honest by not letting one silently follow the other.

It failed. The copies drifted in five undocumented ways, and a third copy fell
out of use entirely with no importer anywhere.

Every permitted difference is now a field on `Scope`:

| Field | Production | Window |
|---|---|---|
| `window_id` | `None` | the window |
| `lookback` | `"24 hours"` | `None` |
| `trend_lookback` | `"90 days"` | `None` |
| `require_labelled_rows` | `True` | `False` |
| `label` | `"Last 24 Hours"` | `"Scenario Window n"` |
| `timestamps_as_iso` | `False` | `True` |

Adding a divergence means adding a field. There is no other route.

`tests/unit/test_query_parity.py` renders all five signals under both scopes and
compares them against SQL frozen from the pre-refactor code, so the queries
behind the published numbers cannot move without a build failure. Two further
tests keep that honest: one asserts the normaliser does not fold JSON key case
(Postgres treats `->> 'clientCountryName'` and `->> 'clientcountryname'` as
different columns), and one asserts the two scopes still produce different SQL —
otherwise the parity tests could pass by over-normalising.

---

## Deliberate inconsistencies

Not bugs. Changing them changes what was measured.

| | Production | Evaluation |
|---|---|---|
| `temperature` | 0.2 | 0.3 |
| prompt | `production_report_v1` (2,675 chars) | `eval_cybersum_v1` (1,379 chars) |
| `###DATA_START###` | present | **absent** |

The evaluation prompt is a shorter variant. It follows that the three-group study
measured the prose contract only and never exercised the side channel — a real
limitation of those results, recorded in
[prompts.md](prompts.md) and asserted by a test so it cannot quietly vanish from
the documentation.
