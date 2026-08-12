# Cybersum

Turns raw security telemetry into a daily briefing written for two audiences at
once — an executive summary and a technical brief, from a single model call.

The interesting part is not the generation. It is what sits in front of it:
five deterministic SQL aggregations that reduce an unbounded log table to a
fixed set of facts, so the model summarises a small structured context instead
of a sample of raw rows. This repository is the system, plus the experiment that
measured whether that actually helps.

```
 telemetry ──► logs (JSONB) ──► 5 SQL signals ──► one model call ──► briefing
  firewall      schema-on-read   fixed size,      dual audience,     + status code
  uptime                         independent of   grounded in the    + top origins
  metrics                        log volume       aggregate          → DB, email, dashboard
```

---

## The finding

A three-group experiment over five synthetic scenario windows, scored blind by
GPT-4o on factual accuracy, completeness and situational awareness (1–5, mean of
three judging runs):

| Group | Input | Prompt | Thesis | Current |
|---|---|---|---|---|
| **A** baseline | 50 raw log rows | short generic prompt | 2.94 | 3.07 |
| **B** control | 50 raw log rows | full structured prompt | 2.07 | **1.69** |
| **C** Cybersum | aggregated context | full structured prompt | 4.09 | **4.93** |

Reading the arrows rather than the rows:

| Comparison | Variable isolated | Thesis | Re-run 1 | Re-run 2 |
|---|---|---|---|---|
| A → B | prompt only, data held constant | −0.87 | −0.87 | **−1.38** |
| B → C | data only, prompt held constant | +2.02 | +3.11 | **+3.25** |
| A → C | combined | +1.15 | +2.25 | **+1.87** |

Three runs over independently regenerated data, judged in three separate
sessions. Read the direction, not the decimals: the prompt effect is negative
every time and lands between −0.87 and −1.38; absolute group means drift by up
to 0.4 between runs with nothing relevant changed. B → C grew across runs
because Group C got better — see below.

**The structured prompt made things worse when applied to raw logs.** Asking a
model for a precise status classification and a specific set of metrics, over a
random sample that mostly does not contain them, produces confident text about
things it cannot see. The same prompt over the aggregated context is the best
performer by a wide margin.

The gap widens exactly where it matters. In the two most severe windows — a
paused service and a critical DDoS score — the baseline scored 2.00 and 1.78
while Cybersum scored 5.00 and 5.00. A random sample of fifty rows tends to miss
low-frequency critical signals, and a briefing that reports a crisis as a quiet
day is worse than no briefing.

Full method, per-window scores and judge rationales: [docs/evaluation.md](docs/evaluation.md).

---

## A grounding failure worth reading

In the thesis run, Group C's factual accuracy topped out at **3.13** while its
completeness reached 4.47. It was seeing everything and still getting a number
wrong — the same number, in all five windows:

| Window | Reported blocked events | Actual | Sum of the top-5 rows |
|---|---|---|---|
| 1 | 1,235 | 2,275 | **1,235** |
| 2 | 4,830 | 9,302 | **4,830** |
| 3 | 5,935 | 11,369 | **5,935** |
| 4 | 6,023 | 11,646 | **6,023** |
| 5 | 6,232 | 12,072 | **6,232** |

The reported figure is the sum of the five-row breakdown, every time. The model
was not missing the total — `total_blocked_events` was the **first field** of the
context it received, and the prompt explicitly asked it to "highlight the total
number of blocked events". Given both an itemised sample and the aggregate that
sample was drawn from, it added up the sample.

That is not a missing-data problem or a missing-instruction problem, and the fix
is not more prompt. It is a context-design problem: an itemised list adjacent to
its own total invites arithmetic.

**Fixed.** The aggregate is now emitted first, computed over exactly the rows the
sample is drawn from, and the sample is labelled as a subset that does not sum to
it. In the re-run, Group C reports the correct total in **5 of 5** windows and
factual accuracy goes **3.13 → 5.00** — which is most of why B → C grew from
+2.02 to +3.11.

Then a deterministic check went in to catch the next one, and immediately did.
It flagged `1,182` in a demo briefing — the sum of the three United States rows
in the five-row breakdown, presented as a country total. Same failure, one level
down. Fixed the same way, by supplying the aggregate the model was otherwise
deriving.

Where that leaves the current run:

| | Grounded figures | Cited per report |
|---|---|---|
| A baseline | 20/27 — 74% | 5.4 |
| B control | 15/23 — 65% | 4.6 |
| **C Cybersum** | **36/36 — 100%** | **7.2** |

Group C is both the most specific and the only one fully traceable. That matters
because the check rewards vagueness: a briefing that cites nothing scores 100%.
Cybersum scores 100% while citing the most.

[docs/findings.md](docs/findings.md) has both, the reproduction, and a note on
where this differs from the explanation given in the thesis.

---

## Run it

Needs Docker and Python 3.11+. No API key, no configuration.

```bash
git clone https://github.com/maevezhang0129/cybersum && cd cybersum
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
make demo
```

About four seconds: starts PostgreSQL, seeds one scenario window (a paused
service and a critical DDoS score), aggregates, generates, and prints the
briefing next to the facts it was given —

```
Ground truth from the aggregation the model was given:
  total blocked events   2,592
  top origin             United States (414 against www.site1.org)
  DDoS health            CRITICAL (risk 62.0, 45.0% malicious)
  services not up        1
      - Main Web Portal: Paused
```

so you can check the prose against the data rather than take its word for it.

Without a key the model call is replayed from a completion recorded from a live
`gpt-4o` call, so the whole parse–format–extract path is real. Export
`OPENAI_API_KEY` and the same command makes a fresh call.

| | |
|---|---|
| `make demo` | database → seed → briefing |
| `make test` | 198 tests, no database, no network |
| `make test-all` | adds 20 integration tests against PostgreSQL |
| `make check` | lint, types, tests, and a scan for internal identifiers |

---

## How the grounding works

Five queries, in [`src/cybersum/aggregation.py`](src/cybersum/aggregation.py):
top attacker origins, total blocked volume, service availability, infrastructure
load, and a 90-day trend. Everything the model ever sees is their output, so
context size is a function of the signal set, not of traffic. Ten thousand rows
and ten million produce the same-sized prompt.

Two design choices carry most of the weight:

- **The aggregate travels with its sample, labelled.** `total_blocked_events` and
  `blocked_by_country` come first, and `top_attacks` is annotated as a five-row
  subset that sums to neither. Wherever a breakdown is available and its
  aggregate is not, the model computes the aggregate and states it as fact.
- **Every figure is traced back before delivery.** A deterministic check pulls
  each number out of the prose and confirms it appears in the context, catching
  the class of error above rather than one instance of it. It reports rather than
  blocks, and it is honest about what it cannot see —
  [docs/grounding.md](docs/grounding.md).
- **Empty is a failure, not an all-clear.** If every signal returns nothing, the
  run stops. That state is indistinguishable from a quiet day, and the previous
  version would have published a reassuring briefing on the morning ingestion
  broke.

Three contracts constrain how far this can be refactored — the
`###DATA_START###` side channel that carries structured fields out of the prose,
the `report_date` unique constraint that makes re-running a day idempotent, and
the single model call serving both audiences. Each is described, and asserted by
a test, in [docs/contracts.md](docs/contracts.md).

## Reproducibility

The published numbers came from code that has since been unified and refactored.
To keep the claim that this is the same system honest:

- The ten aggregation queries were frozen to
  [`tests/golden/sql/`](tests/golden/sql) before any refactoring. A parity test
  renders all five signals under both scopes and compares them to those files,
  so the merge cannot silently change what the thesis measured.
- Prompt texts live in files pinned by sha256 in
  [`PROMPTS.lock.json`](src/cybersum/prompts/PROMPTS.lock.json). Reword one
  without updating the lock and the build fails.
- [`evaluation/outputs/published/`](evaluation/outputs/published) holds the
  thesis results unchanged, with a README documenting a real drift: the recorded
  ground-truth context contains a field no surviving code emits, so those exact
  numbers are archive rather than something the current harness reproduces.
- [`evaluation/outputs/runs/2026-08-12/`](evaluation/outputs/runs/2026-08-12) is
  what the current code actually produces, from `python -m evaluation.cli run`.
  `evaluation.cli compare` puts the two side by side.

## What this does not show

- **One judge, unvalidated.** Scores come from GPT-4o judging GPT-4o output. No
  human evaluation was run to check the judge agrees with people.
- **The side channel was never under test.** The evaluation prompt is a shorter
  variant that omits the `###DATA_START###` instruction, so the three-group study
  measured the prose contract only. See [docs/prompts.md](docs/prompts.md).
- **Synthetic data.** Five fabricated windows modelled on CICIDS 2018 traffic
  characteristics, not a real attack corpus.
- **Five windows, three judging runs.** Enough to separate a 2.02-point effect
  from noise; not enough for a confidence interval worth quoting.

## Deployment

The system ran daily inside an international organisation's cloud tenant between
November 2025 and March 2026. Access ended with the internship, so this is
recorded rather than re-runnable: 20 consecutive successful invocations over
seven days, mean end-to-end latency 5,899 ms, roughly 3,361 prompt and 322
completion tokens per run — about **USD 0.0116 a run**, or **USD 4.23 a year**
against roughly USD 1,152 for an always-on VM.

Nothing from that environment is in this repository: no hostnames, addresses,
credentials, screenshots or telemetry. The Azure Functions entry point in
[`adapters/azure_function/`](adapters/azure_function) is the deployment shape,
59 lines translating between the runtime and the pipeline.

## Documentation

| | |
|---|---|
| [architecture.md](docs/architecture.md) | the three layers, and the schema-on-read weakness |
| [findings.md](docs/findings.md) | the grounding failure, twice, and both fixes |
| [grounding.md](docs/grounding.md) | the deterministic check, and what it cannot tell you |
| [evaluation.md](docs/evaluation.md) | method, per-window scores, limitations |
| [contracts.md](docs/contracts.md) | four properties a refactor breaks quietly |
| [prompts.md](docs/prompts.md) | why two report prompts exist and stay separate |

## Research origin

This began as a master's thesis at Stockholm University, Department of Computer
and Systems Sciences. The thesis covers the design rationale, the literature the
approach draws on, and the full evaluation.

> Master's thesis, Department of Computer and Systems Sciences, Stockholm
> University, 2026. Defended. The DiVA record is pending publication; the
> citation and link will be added here once it is available.

Where this repository and the thesis disagree — the cause of the accuracy
ceiling, and the reproducibility of the published numbers — the repository
states the current reading and says so explicitly. See
[docs/findings.md](docs/findings.md).

## Roadmap

Next, in order:

1. **Make the grounding check a gate.** It reports today. Blocking delivery on an
   unexplained figure needs a false-positive rate measured over more than fifteen
   briefings first.
2. **Field-level attribution.** The check matches values; it cannot tell that a
   figure lifted from the trend data has been described as a 24-hour total. That
   needs each figure tied to the field it came from.
3. **Evaluation as a gate.** `make eval` in CI over a fixed fixture, failing when
   the score drops.
4. **Model comparison.** The provider seam is already there; running the same
   evaluation across models is a small step and a useful result.
5. **Human validation of the judge.** The thesis planned a blind human panel and
   used G-Eval instead. Closing that gap is what would make the scores mean
   something outside this repository.

## Licence

MIT. See [LICENSE](LICENSE).
