# A model that would rather add up than read

## What happened

In the thesis evaluation, Group C — aggregated context, full prompt — reached
4.47 on completeness and 4.67 on situational awareness, but stalled at **3.13 on
factual accuracy**. It was seeing everything and still getting something wrong.

It is fixed; the fix and its measurement are at the end. The diagnosis is the
part worth reading.

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

First, a single window, one call each, `gpt-4o` at temperature 0.2, identical
prompt, the only difference being whether the aggregate and its note are present:

| Context | Reported |
|---|---|
| ground truth | **2,592** |
| without `total_blocked_events` | 1,500 |
| with `total_blocked_events` | **2,592** |

Two things. The fix produced the exact figure. And without the aggregate the
model did not fall back on the top-five sum (1,397) — it reported 1,500, lifted
from the trend data. Deprived of the right answer it will find *some* number, and
the specific wrong number is not stable. That is its own argument against
treating a plausible figure as a grounded one.

### The full re-run

The three-group evaluation was then re-run over all five windows with the fixed
aggregation (`evaluation/outputs/runs/2026-08-12/`). Group C now reports the
correct total in **five windows out of five**:

| Window | Truth | Sum of top-5 | Reported |
|---|---|---|---|
| 1 | 2,304 | 1,192 | **correct** |
| 2 | 9,240 | 4,738 | **correct** |
| 3 | 11,283 | 5,766 | **correct** |
| 4 | 11,716 | 6,104 | **correct** |
| 5 | 12,122 | 6,241 | **correct** |

And the dimension that was stuck moved:

| | Thesis run | Re-run |
|---|---|---|
| Group C factual accuracy | 3.13 | **5.00** |
| Group C overall | 4.09 | **4.91** |
| B → C effect | +2.02 | **+3.11** |

The ceiling was this error and nothing else. With it gone, Group C scores 5.00 on
factual accuracy in every window.

### What the re-run also says about the rest

Absolute levels drifted between runs — Groups A and B each fell 0.27 — which is
what a different data seed and a different judging session look like. The
**prompt effect did not**: A → B is −0.87 in both runs, because A and B moved
together. An effect that survives regenerated data and independent judging is
more believable than one measured once.

Two caveats worth keeping. Five perfect scores invite suspicion, so the judge's
rationales were read rather than assumed: they name the paused service, the DDoS
status and the blocked total specifically, rather than offering generic praise.
And nothing *enforces* the correct total — Group C scoring 5.00 is a property of
this model on this data, not a guarantee. That is why a deterministic numeric
grounding check is the first item on the roadmap rather than a nice-to-have.

## The check found a second instance

With the total fixed, the deterministic check went into the pipeline and
immediately flagged a figure in the demo briefing: **1,182**, presented as
"traffic originated: United States: 1,182".

It is not in the context. It is the sum of the three United States rows in
`top_attacks`:

| host | country | blocked |
|---|---|---|
| www.site1.org | United States | 414 |
| api.site2.org | United States | 309 |
| login.site3.org | United States | 248 |
| www.site1.org | China | 215 |
| cdn.site4.org | United States | 211 |

414 + 309 + 248 + 211 = 1,182.

The same failure, one level down. The arithmetic is correct; the claim is not.
`top_attacks` is the five busiest *host/country pairs*, so a country's traffic
against any host outside those five contributes nothing — "United States: 1,182"
reads as a country total and is a floor.

The fix is the same shape: give the model the aggregate it would otherwise have
to derive. `blocked_by_country` is a `GROUP BY country` over every row, sitting
beside the total, with the note extended to say the country column of
`top_attacks` does not sum to it either.

Afterwards the same briefing traces **12 of 12** figures, up from 7 of 8 — and
cites *more* numbers than before, because it now has a country breakdown worth
citing rather than one it had to invent.

Two things worth taking from this. The pattern generalises: **wherever a
breakdown is available and its aggregate is not, the model will compute the
aggregate and state it as fact.** And a deterministic check earns its place by
finding new instances, not only by guarding known ones — this one was found in a
demo run, not in an experiment designed to look for it.

## What it suggests more generally

- **Adjacency is instruction.** Putting a breakdown next to a request for a total
  is a prompt, whatever the prompt says. Context layout is not neutral packaging.
- **Correct data is not sufficient for grounding.** The right number being
  present is necessary and demonstrably not enough. Grounding is about which fact
  is easiest to reach.
- **This class of error is invisible downstream.** A hallucinated total is
  well-formed and plausible. Catching it needs a check that ties each figure in
  the prose back to a field in the context — which is why that is the first item
  on the roadmap.
- **The evaluation earned its keep by finding a bug.** Not by producing a score.
  An LLM evaluation harness that only produces scores is a report; one that
  surfaces a reproducible defect is a test suite.
- **Fixing an instance is not fixing the class.** The total was one place where a
  breakdown sat next to a missing aggregate. The country figure was another, and
  it was found within minutes of the check going live. Auditing the context for
  that shape is cheaper than waiting for each one to surface.
