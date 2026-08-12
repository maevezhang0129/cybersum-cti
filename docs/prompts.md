# Prompts

Four texts, in [`src/cybersum/prompts/`](../src/cybersum/prompts), each pinned by
sha256 in `PROMPTS.lock.json`.

| File | Used by | Chars |
|---|---|---|
| `production_report_v1.txt` | the daily pipeline | 2,675 |
| `eval_cybersum_v1.txt` | evaluation groups B and C | 1,379 |
| `eval_baseline_v1.txt` | evaluation group A | 178 |
| `geval_rubric_v3.txt` | the judge, both scoring paths | 3,786 |

## Why they are files, and why they are locked

Prompt text is the thing an evaluation measures. Once it lives in a `.txt` it
becomes reviewable and diffable — and also easy to tidy up without thinking,
which would change what a published number means with no code diff to show for
it. The lockfile makes that a build failure instead.

Updating a prompt is fine. Update its hash in the same commit, and the diff tells
a reader that the measured artifact moved.

The three evaluation prompts are **verbatim** from the code that produced the
published results — no edits, not even desensitising ones. The production prompt
carries exactly one substitution, replacing the host organisation's name with a
neutral label. It produced no published number, and the lockfile records both the
substitution and the pre-edit hash.

## The two report prompts are not merged

`production_report_v1` is roughly twice the length of `eval_cybersum_v1` and
differs in substance, not wording. The production prompt additionally carries:

- the plain-text formatting rules for the dashboard, which renders no markdown;
- the political-neutrality clause (attacks are attributed to the geolocation of
  source addresses, never to states);
- the entire `###DATA_START###` instruction.

**Consequence: the three-group study never exercised the machine-readable side
channel.** Groups B and C used the evaluation prompt, which does not ask for the
block. Those results measure the prose contract — the dual-audience structure,
the status classification, the grounding — and say nothing about whether the
structured fields extract reliably.

That is a real limitation of the published results and the reason the two files
stay separate. Merging them would make the repository tidier and the numbers
false. A test records it:

```python
def test_only_the_production_prompt_carries_the_data_side_channel():
    carries = {n for n in prompt_names() if "###DATA_START###" in load_prompt(n).text}
    assert carries == {"production_report_v1"}
```

## What the production prompt asks for

Five decisions, in the order they appear:

1. **An institutional persona.** Sets register and, importantly, constrains
   attribution: source-address geolocation is a fact, state attribution is not.
2. **Deterministic status classification.** Explicit if-then criteria for
   `STABLE` / `STATUS A` / `STATUS B` / `STATUS C`, rather than leaving severity
   to the model's judgement. The status drives an email subject line and a
   dashboard colour, so it has to be reproducible.
3. **Plain-text formatting.** No markdown, uppercase section headers, hyphens for
   bullets. Driven by the dashboard, which renders text verbatim.
4. **Two labelled sections in one response.** `1: EXECUTIVE SUMMARY` and
   `2: TECHNICAL BRIEF` — the dual-audience mechanism, from a single call.
5. **The appended data block.** Structured fields for the database and the
   dashboard, fenced by delimiters and stripped from the prose before anyone
   reads it.

Points 3 and 5 are why the prompt is twice the length of the evaluation variant,
and both are downstream-integration concerns rather than quality ones.

## Temperature

0.2 in production, 0.3 in the evaluation. Deliberate and left alone: the
evaluation wanted variance across its three judging runs, the daily job wants
the same input to produce broadly the same briefing. Both live in `LLMSettings`
with the evaluation's override visible at the call site rather than buried in a
second config file.
