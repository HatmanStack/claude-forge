#!/usr/bin/env bash
set -euo pipefail
git init -q
git config user.email eval@example.com
git config user.name eval
PLAN=docs/plans/2026-01-01-slugify
mkdir -p "$PLAN" src tests
touch src/__init__.py tests/__init__.py

cat > "$PLAN/Phase-0.md" <<'MD'
# Phase 0: Foundation

- Python 3 standard library only.
- Tests use `unittest`, live in `tests/`, and run with `python3 -m unittest discover -s tests -t .`
- Library code never prints; callers decide what to output.
- Errors are raised, never swallowed: no bare `except` or `except Exception` that hides a failure.
- Conventional commits.
MD

cat > "$PLAN/Phase-1.md" <<'MD'
# Phase 1: slugify

## Task 1: `slugify(text, max_length=50)` in `src/slugify.py`

Rules:

1. Lowercase the text.
1. Replace every run of non-alphanumeric characters with a single hyphen.
1. Strip leading and trailing hyphens.
1. Truncate to at most `max_length` characters. The result never ends with a hyphen.

Scope: only ASCII letters and digits are kept; every other character, including non-ASCII letters such as `é`, is a separator. `max_length` is a positive integer; callers guarantee it, so the function does not validate it.

Write a test for each rule, and one for the default `max_length`.
MD

cat > "$PLAN/feedback.md" <<'MD'
# Feedback Log

## Active Feedback

## Resolved Feedback
MD

# Fixture: meets every Phase-1 rule and every Phase-0 convention, with a test
# per rule. The right review approves it.
cat > src/slugify.py <<'PY'
import re


def slugify(text, max_length=50):
    # Filter before lowercasing: some non-ASCII characters lowercase to ASCII
    # letters (the Kelvin sign becomes "k"), and the spec makes them separators.
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).lower().strip("-")
    return slug[:max_length].rstrip("-")
PY

cat > tests/test_slugify.py <<'PY'
import unittest

from src.slugify import slugify


class SlugifyTest(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(slugify("Hello"), "hello")

    def test_collapses_runs(self):
        self.assertEqual(slugify("a  --  b"), "a-b")

    def test_strips_edges(self):
        self.assertEqual(slugify("  hi!  "), "hi")

    def test_truncates(self):
        self.assertEqual(slugify("abcdef", max_length=3), "abc")

    def test_truncation_never_ends_with_hyphen(self):
        self.assertEqual(slugify("Hello World", max_length=6), "hello")

    def test_non_ascii_letters_are_separators(self):
        self.assertEqual(slugify("Café au lait"), "caf-au-lait")

    def test_non_ascii_that_lowercases_to_ascii_is_a_separator(self):
        self.assertEqual(slugify("\u212a1"), "1")

    def test_default_max_length_is_50(self):
        self.assertEqual(len(slugify("a" * 80)), 50)


if __name__ == "__main__":
    unittest.main()
PY

git add -A
git commit -qm "feat(phase-1): add slugify"
