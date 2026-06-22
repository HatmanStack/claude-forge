#!/usr/bin/env python3
"""Tier C / Tier D — run trajectory validators against a real pipeline run.

The trace hook writes `trace-summary.json` (roles, governance-signal events, and
security findings) into its per-session state dir. This script replays the
protocol invariants over that artifact, so nightly / pre-release jobs assert on
*actual* execution order rather than assumed order.

Usage:
    python evaluation/check_run.py path/to/trace-summary.json
    python evaluation/check_run.py --latest      # newest under the tracing tmp dir

Exit code is non-zero if any trajectory violation is found.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import trajectory  # noqa: E402


def _latest_summary():
    root = Path(tempfile.gettempdir()) / "claude-forge-tracing"
    summaries = sorted(root.glob("*/trace-summary.json"), key=lambda p: p.stat().st_mtime)
    return summaries[-1] if summaries else None


def main():
    ap = argparse.ArgumentParser(description="Validate a Forge pipeline run trajectory.")
    ap.add_argument("summary", nargs="?", help="path to trace-summary.json")
    ap.add_argument("--latest", action="store_true", help="use the newest trace-summary.json")
    args = ap.parse_args()

    path = Path(args.summary) if args.summary else (_latest_summary() if args.latest else None)
    if not path or not path.exists():
        print("no trace-summary.json found (run a pipeline with CLAUDE_FORGE_TRACING=1 first)")
        return 2

    data = json.loads(path.read_text())
    events = data.get("events", [])
    violations = trajectory.validate_trajectory(events)
    sec = (data.get("security") or {}).get("findings", [])

    print(f"trajectory: {len(events)} governance events from {path}")
    for ev in events:
        print(f"  {ev.get('role')}: {ev.get('signal')}")

    if sec:
        print(f"\nsecurity findings: {len(sec)}")
        for f in sec:
            print(f"  [{f.get('severity')}] {f.get('dp')}.{f.get('kind')} — {f.get('detail')}")

    if violations:
        print(f"\nTRAJECTORY VIOLATIONS: {len(violations)}")
        for v in violations:
            print(f"  {v['kind']}: {v['detail']}")
        return 1

    print("\ntrajectory OK — no protocol violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
