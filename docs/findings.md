# A model that would rather add up than read

## What happened

In the three-group evaluation, Group C — aggregated context, full prompt — reached
4.47 on completeness and 4.67 on situational awareness, but stalled at **3.13 on
factual accuracy**. It was seeing everything and still getting something wrong.

The same thing, in all five scenario windows:

| Window | Scenario | Reported | Actual `total_blocked_events` | Sum of `top_attacks` |
|---|---|---|---|---|
| 1 | STABLE | 1,235 | 2,275 | **1,235** |
| 2 | STATUS_A | 4,830 | 9,302 | **4,830** |
| 3 | STATUS_B | 5,935 | 11,369 | **5,935** |
| 4 | STATUS_C | 6,023 | 11,646 | **6,023** |
| 5 | STATUS_C | 6,232 | 12,072 | **6,232** |

Every reported figure is the sum of the five-row `top_attacks` breakdown,
exactly. Not approximately: to the unit, five times out of five. The judge caught
it each time — *"The report underestimates the total blocked events as 1,235
instead of 2,275."*

Reproduce it from the committed data:

```bash
python - <<'PY'
import json
for r in json.load(open("evaluation/outputs/published/all_reports.json")):
    ctx = r["aggregated_context"]
    print(r["window_id"],
          ctx["total_blocked_events"],
          sum(a["n"] for a in ctx["security_summary"]["top_attacks"]))
PY
```

## Why it is not the obvious explanations

**It was not missing data.** `total_blocked_events` was present. It was the
*first key* of the context object.

**It was not a missing instruction.** The prompt says, in the executive summary
section: *"Highlight the total number of blocked events and the top countries."*

**It was not a sampling artefact.** Group C received the aggregate, not a sample.
The correct number was in front of the model and it computed a different one.

What is left is the shape of the context. `top_attacks` is an itemised list of
counts. Placed near a request for a total, an itemised list of counts is an
invitation to add. The model took the more legible arithmetic path over the less
salient lookup — and produced a number that is internally consistent, plausible,
and roughly half the truth. Nothing downstream could have caught it: it is not
malformed, not out of range, not obviously odd.

## Where this differs from the thesis

The thesis attributes the accuracy ceiling to the aggregation, stating that
`get_firewall_stats` returns top-five host/country groupings rather than total
blocked volume, so the reports systematically understate the aggregate.

The committed data does not support that. `total_blocked_events` is present in
every recorded `aggregated_context` in `all_reports.json`, so the total was not
absent from the aggregation as it ran. The behaviour is the model's, not the
query's.

This does not change the thesis's results or its conclusions — the ceiling is
real, the effect sizes stand, and the recommendation to ground generation in
deterministic aggregates is if anything strengthened. It changes the diagnosis,
and therefore the fix. The thesis is defended and unaltered; this repository
states the reading the data supports.

There is a related wrinkle. `total_blocked_events` appears in the recorded
contexts but is produced by **no code that survived into the repository** — the
aggregation was edited after the experiment ran and the results were never
regenerated. See `evaluation/outputs/published/README.md`.

## The fix

Three changes, in [`src/cybersum/aggregation.py`](../src/cybersum/aggregation.py):

1. `total_blocked_sql` — a plain `COUNT(*)` over exactly the rows the top-five
   breakdown covers, so the two can never disagree about scope.
2. The total is emitted **first**, before the sample it summarises.
3. `top_attacks` carries an adjacent note: *"top_attacks is the 5 busiest
   host/country pairs only, not the full breakdown; it does not sum to
   total_blocked_events"*.

An integration test asserts the arithmetic that made the failure possible — with
more than five host/country pairs, the top-five list sums to strictly less than
the total:

```python
def test_total_is_larger_than_the_sum_of_the_top_five(db):
    ...
    assert sum(r["block_count"] for r in top5) < total
```

## Does the fix work

One window, one call each, `gpt-4o` at temperature 0.2, identical prompt, the
only difference being whether the aggregate and its note are present:

| Context | Reported |
|---|---|
| ground truth | **2,592** |
| without `total_blocked_events` | 1,500 |
| with `total_blocked_events` | **2,592** |

Two observations. The fix produced the exact figure. And without the aggregate
the model did not fall back on the top-five sum (1,397) — it reported 1,500, a
number lifted from the trend data. Deprived of the right answer it will find
*some* number; the specific wrong number is not stable, which is its own argument
against treating a plausible figure as a grounded one.

This is n=1 per arm. It shows the failure reproduces and the change removes it in
that instance. It is not a measurement of effect size. Re-running the full
three-group evaluation is the first item on the roadmap, and until it is done the
score table in the README reports the original numbers, not improved ones.

## What it suggests more generally

- **Adjacency is instruction.** Putting a breakdown next to a request for a total
  is a prompt, whatever the prompt says. Context layout is not neutral packaging.
- **Correct data is not sufficient for grounding.** The right number being
  present is necessary and demonstrably not enough. Grounding is about which fact
  is easiest to reach.
- **This class of error is invisible downstream.** A hallucinated total is
  well-formed and plausible. Catching it needs a check that ties each figure in
  the prose back to a field in the context — which is why that is roadmap item 2
  rather than a nice-to-have.
- **The evaluation earned its keep by finding a bug.** Not by producing a score.
  An LLM evaluation harness that only produces scores is a report; one that
  surfaces a reproducible defect is a test suite.
