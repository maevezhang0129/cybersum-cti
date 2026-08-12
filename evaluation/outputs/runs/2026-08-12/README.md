# Re-run, 12 August 2026

The three-group experiment on the current code, produced by:

```bash
make seed-all
python -m evaluation.cli run --yes --out evaluation/outputs/runs/2026-08-12
```

gpt-4o, generation at temperature 0.3, three judging passes per briefing at 0.3,
data seeded at 42 with a base date of 2026-03-10.

Unlike `../../published/`, this is reproducible: the code that wrote these files
is the code in the repository, and the generator is seeded.

## What changed against the thesis run

| | Thesis | Here |
|---|---|---|
| Group A | 2.94 | 3.07 |
| Group B | 2.07 | **1.69** |
| Group C | 4.09 | **4.93** |
| A → B | −0.87 | **−1.38** |
| B → C | +2.02 | **+3.25** |
| A → C | +1.15 | **+1.87** |

Group C scores 5.00 on factual accuracy in every window, after the two grounding
fixes described in `docs/findings.md`: it reports the correct blocked total and
the correct per-country totals rather than deriving either from the five-row
breakdown.

Group means drift between runs — Group A has been 2.94, 2.67 and 3.07 across
three runs with nothing about Group A changing. Compare the arrows, not the
levels, and treat the second decimal as noise.

Grounding, from the deterministic check rather than the judge:

| | Grounded | Cited per report |
|---|---|---|
| A | 20/27 — 74% | 5.4 |
| B | 15/23 — 65% | 4.6 |
| C | **36/36 — 100%** | **7.2** |

The grounding records in `all_reports.json` were backfilled after the run, using
the harness's own `_grounding_record` over the reports and contexts already in
the file. The check is deterministic over those two inputs, so the values are
what the harness would have written had it recorded them at the time.

Only `avg_score` is the mean of the three dimensions; the dimensions themselves
are means of three judging passes.
