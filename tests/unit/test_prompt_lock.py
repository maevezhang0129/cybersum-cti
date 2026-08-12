"""Prompt texts are pinned by hash.

The published evaluation numbers are a measurement of specific prompt wording.
Once that wording lives in an editable text file, a well-meaning tidy-up can
change what the numbers mean without changing a line of code. These tests make
that a build failure rather than a silent one.

Updating a prompt is fine -- update the lockfile in the same commit, and the
diff will show a reviewer that the measured artifact moved.
"""

from __future__ import annotations

import pytest

from cybersum.prompts import load_lockfile, load_prompt, prompt_names

LOCK = load_lockfile()


def test_lockfile_and_directory_agree_on_which_prompts_exist() -> None:
    assert sorted(LOCK) == prompt_names()


@pytest.mark.parametrize("name", sorted(LOCK))
def test_prompt_matches_locked_hash(name: str) -> None:
    prompt = load_prompt(name)
    assert prompt.sha256 == LOCK[name]["sha256"], (
        f"{name}.txt changed. If that was deliberate, update its sha256 in "
        f"PROMPTS.lock.json in the same commit."
    )


@pytest.mark.parametrize("name", sorted(LOCK))
def test_locked_length_matches(name: str) -> None:
    assert len(load_prompt(name).text) == LOCK[name]["chars"]


@pytest.mark.parametrize("name", ["eval_cybersum_v1", "eval_baseline_v1", "geval_rubric_v3"])
def test_evaluation_prompts_are_verbatim_from_the_thesis_run(name: str) -> None:
    """These three produced the numbers in evaluation/outputs/published/, so
    they carry no edits at all -- not even desensitising ones."""
    assert LOCK[name]["verbatim"] is True


def test_production_prompt_records_its_one_edit() -> None:
    """The production prompt named the host organisation. It never produced a
    published number, so substituting a neutral label is safe -- but the
    lockfile has to say it happened, and keep the pre-edit hash."""
    entry = LOCK["production_report_v1"]
    assert entry["verbatim"] is False
    assert entry["edits"], "an edited prompt must record what was edited"
    assert entry["sha256_before_edits"] != entry["sha256"]


def test_only_the_production_prompt_carries_the_data_side_channel() -> None:
    """The ###DATA_START### block is how status_code and top_5_origins reach the
    database and the dashboard. It is absent from the evaluation prompt, which
    means the three-group study measured the prose contract only and never
    exercised the side channel. Recorded here so the limitation cannot quietly
    disappear from the documentation.
    """
    carries = {n for n in prompt_names() if "###DATA_START###" in load_prompt(n).text}
    assert carries == {"production_report_v1"}


def test_no_prompt_names_the_host_organisation() -> None:
    """Redundant with the repository-wide scan, and kept anyway: the production
    prompt is the one file here that was edited to remove a name, so it is worth
    checking at the point the edit was made. Patterns come from .denylist rather
    than a second copy -- a duplicated denylist is one that drifts."""
    from .test_no_internal_identifiers import DENY

    for name in prompt_names():
        match = DENY.search(load_prompt(name).text)
        assert match is None, f"{name}.txt names an internal identifier"
