#!/usr/bin/env bash
# Fixture: Phase 1 is committed and its tests pass, but src/slugify.py
# truncates after stripping, so a cut can end on a hyphen, violating the
# Phase-1 rule "never ends with a hyphen". No test covers that rule.
set -euo pipefail
git init -q
git config user.email eval@example.com
git config user.name eval
PLAN=docs/plans/2026-01-01-slugify
mkdir -p "$PLAN" src tests

cat > "$PLAN/Phase-0.md" <<'MD'
# Phase 0: Foundation

- Python 3 standard library only.
- Tests use `unittest`, live in `tests/`, and run with `python3 -m unittest discover -s tests -t .`
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

Write a test for each rule.
MD

cat > "$PLAN/feedback.md" <<'MD'
# Feedback Log

## Active Feedback

## Resolved Feedback
MD

touch src/__init__.py tests/__init__.py
cat > src/slugify.py <<'PY'
import re


def slugify(text, max_length=50):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length]
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


if __name__ == "__main__":
    unittest.main()
PY

git add -A
git commit -qm "feat(phase-1): add slugify"
