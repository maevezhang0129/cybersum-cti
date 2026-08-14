"""No identifier belonging to the deployment environment may reach the tree.

This code was extracted from a system that ran inside an international
organisation. What leaks in that situation is usually not a credential -- those
were in gitignored files and never committed -- but *identifiers*: internal
hostnames, a mail relay, private addresses, resource names. Credential scanners
do not look for them, because they are not secrets. They are still not ours to
publish.

The patterns live in ``.denylist`` at the repository root, which is the one file
allowed to contain them.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
DENYLIST_FILE = REPO / ".denylist"

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", "node_modules"}

# Files whose whole purpose is to name the patterns.
SELF_REFERENTIAL = {DENYLIST_FILE, pathlib.Path(__file__).resolve()}

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".gif", ".ico", ".zip", ".pyc"}


def patterns() -> list[str]:
    return [
        line.strip()
        for line in DENYLIST_FILE.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


DENY = re.compile("|".join(patterns()), re.IGNORECASE)


def tracked_files() -> list[pathlib.Path]:
    found = []
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        if path.resolve() in SELF_REFERENTIAL:
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        found.append(path)
    return found


def binary_files() -> list[pathlib.Path]:
    return sorted(
        path.relative_to(REPO)
        for path in REPO.rglob("*")
        if path.is_file()
        and not SKIP_DIRS & set(path.parts)
        and path.suffix.lower() in BINARY_SUFFIXES
    )


# An image can carry an internal hostname, an address bar or a real name in
# pixels, and no regex will see it. Nothing here can scan one -- so instead the
# set is pinned. A new image cannot appear in the repository without a person
# adding it to this list, which is the point at which they have to look at it.
REVIEWED_IMAGES = {
    pathlib.Path("docs/images/dashboard.png"),  # make dashboard, seed 42, synthetic
}


def test_every_committed_image_has_been_looked_at() -> None:
    unreviewed = set(binary_files()) - REVIEWED_IMAGES
    assert not unreviewed, (
        f"{sorted(map(str, unreviewed))} cannot be scanned for internal "
        "identifiers. Open each one, confirm it shows nothing belonging to the "
        "deployment environment, then add it to REVIEWED_IMAGES."
    )


def test_denylist_is_not_empty() -> None:
    """A scan over an empty pattern list passes trivially."""
    assert len(patterns()) >= 5


def test_the_scan_can_actually_fail() -> None:
    """Guards the guard: confirm the pattern matches something it should."""
    assert DENY.search("host is mx-westeurope.example")
    assert not DENY.search("host is mail.example.org")


@pytest.mark.parametrize(
    "path", tracked_files(), ids=lambda p: str(p.relative_to(REPO))
)
def test_file_names_no_internal_identifier(path: pathlib.Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        pytest.skip("not readable as text")

    hits = [
        (i + 1, DENY.search(line).group(0))  # type: ignore[union-attr]
        for i, line in enumerate(text.splitlines())
        if DENY.search(line)
    ]
    assert not hits, (
        f"{path.relative_to(REPO)} names an internal identifier at "
        f"line(s) {[h[0] for h in hits]}"
    )
