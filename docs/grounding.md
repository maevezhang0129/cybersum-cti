# The grounding check

Every figure in a generated briefing is traced back to the data the model was
given, deterministically, before the briefing is delivered.

No model is involved. A check that asks an LLM whether an LLM was truthful can
be wrong in the same direction as the thing it is checking; this one compares
numbers against a set.

## Why

The evaluation found the model reporting a total it had computed rather than
read — in all five scenario windows, the sum of a five-row breakdown presented as
the whole. The figure was well-formed, in a plausible range, and internally
consistent. Nothing downstream could have caught it: not the parser, not the
schema, not a human skimming a daily briefing.

Adding the aggregate to the context fixed that instance. It does not prevent the
next one, and there was a next one — see [findings.md](findings.md).

## How it works

**Facts.** Every numeric leaf in the aggregated context, recursively. Values
arriving as strings from `NUMERIC` columns are parsed. Clock times and dates are
pulled out of timestamp strings separately, since `"2026-03-10 18:00"` is not a
number. A ratio between 0 and 1 also registers as its percentage, because a
`malicious_ratio` of 0.45 is "45%" in the prose.

**Figures.** Every number in the briefing, classified as a quantity, percentage,
clock time or date. Ordering matters: `18:00` is read as one clock time, not as
`18` and `00`.

**Matching.** Numeric, not textual — `100` must not match by appearing inside
`10029.99`. A relative tolerance of 0.5% accepts the rounding the model actually
does: 10,029.99 written as "10,030" is correct prose and must not be flagged.

## What it deliberately ignores

Calibrated against the fifteen committed briefings, because each of these was a
false positive on real output before it was excluded:

| Ignored | Why |
|---|---|
| digits in hostnames and URLs | `www.site1.org` is not a claim about 1 |
| list markers (`1.`, `2)`) | structure, not data |
| `STATUS A/B/C` | vocabulary, not measurement |
| "the last 24 hours", "over the last 90 days" | the reporting window is a property of the request; every briefing repeats it |
| quantities ≤ 10 | ordinals and counts of visible things: "the top 5 origins", "3 services" |

Small numbers are counted as *skipped* rather than *grounded*, so the
denominator stays honest.

## What it does not tell you

**It measures traceability, not quality.** A briefing that cites nothing scores
100%. In the current run Group A — raw logs, generic prompt, factual accuracy
2.20 — traces 96% of its figures, because it cites 4.8 per report against Group
C's 6.4 and hedges the rest. That is not a defect in the check; it is the reason
it complements the evaluation rather than replacing it.

**It does not check that a figure means what the prose says it means.** A number
lifted from the trend data and described as a 24-hour total is grounded and
wrong. Catching that needs field-level attribution, not value matching.

**It cannot see omissions.** A briefing that never mentions the paused service
passes.

## What happens when it fails

It reports; it does not block. A briefing with one unexplained figure is still
more useful than no briefing, and blocking on a false positive would be a worse
failure than the one being prevented. The result travels on `PipelineResult`,
appears in the run summary, and is logged at WARNING with the surrounding
sentence so a human can adjudicate:

```
Grounding check (deterministic, no model involved):
  1 of 8 figures could not be traced:
            1182  ...traffic originated: - United States: 1182 - China: 215
```

Turning this into a delivery gate is a policy decision, and a reasonable one
once the false-positive rate is known across more than fifteen briefings.

## Testing

`tests/unit/test_grounding.py`. The acceptance test is the one that matters: it
runs the check over the five committed thesis-run briefings and requires it to
flag the fabricated total in **every** one. A check that cannot catch the failure
it was built for is decoration.

A companion test asserts the current Group C briefings are fully grounded, so
the number quoted in the README is verified rather than remembered.
