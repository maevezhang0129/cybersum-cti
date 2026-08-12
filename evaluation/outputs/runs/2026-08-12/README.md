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
| Group A | 2.94 | 2.67 |
| Group B | 2.07 | 1.80 |
| Group C | 4.09 | **4.91** |
| A → B | −0.87 | **−0.87** |
| B → C | +2.02 | **+3.11** |

Group C's factual accuracy went from 3.13 to 5.00 after the grounding fix
described in `docs/findings.md`; it now reports the correct blocked total in all
five windows. Groups A and B each fell 0.27, which is what a different data seed
and a different judging session look like — so compare the arrows, not the
levels.

Only `avg_score` is the mean of the three dimensions; the dimensions themselves
are means of three judging passes.
