---
type: llm
weight: 2
focus: { source: file, path: docs/plans/2026-01-01-slugify/feedback.md }
---

`src/slugify.py` calls `print(...)` inside `slugify`, breaking the Phase-0 rule that library code never prints.

PASS if the feedback flags the print/debug output in library code.
FAIL if it does not mention it, or the file has no review entries.
