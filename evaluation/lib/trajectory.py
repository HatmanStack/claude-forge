"""Tier C trajectory checks for Claude Forge.

A final report can read "GO — all phases approved" even when a gate was skipped
or a signal was forged. Quality lives in the trajectory, not the final answer.
These validators operate on the sequence of governance signals a pipeline run
emitted (the same data the trace hook records as `forge.security.signal_timeline`
and persists to `trace-summary.json`), so the same logic powers both the
deterministic per-PR unit tests and live-run checks.

An `events` trajectory is a list of dicts: {"role": <bare role>, "signal": <TOKEN>}
in chronological order.
"""

from lib import registry


def score_tool_trajectory_in_order(actual, expected):
    """Return 1.0 if every tool/step in `expected` appears in `actual` in order
    (subsequence match), else 0.0. Ported from the evaluation-pyramid pattern:
    deliberately simple so a pass/fail is always explainable."""
    idx = 0
    for step in actual:
        if idx < len(expected) and step == expected[idx]:
            idx += 1
    return 1.0 if idx == len(expected) else 0.0


def _violation(kind, detail, index=None):
    return {"kind": kind, "detail": detail, "index": index}


def check_signal_provenance(events):
    """A gate-passing signal may only be cast by an authorized role. A generator
    or assessor emitting one is a forged ballot (DP3)."""
    out = []
    for i, ev in enumerate(events):
        sig, role = ev.get("signal"), ev.get("role")
        allowed = registry.ADVANCE_EMITTERS.get(sig)
        if allowed is not None and role not in allowed:
            out.append(_violation(
                "signal_forgery",
                f"{role} cast {sig}; authorized roles: {sorted(allowed)}",
                i,
            ))
    return out


def check_pipeline_order(events):
    """Ordering invariants that hold across all Forge pipeline types:

    - each PLAN_APPROVED must consume a fresh, not-yet-used PLAN_COMPLETE
    - no PHASE_APPROVED before the plan is approved
    - GO/VERIFIED must be preceded by at least one PHASE_APPROVED
    """
    out = []
    pending_plan = 0  # unconsumed PLAN_COMPLETE credits; one per approval
    plan_approved = False
    phase_approved_seen = False
    for i, ev in enumerate(events):
        sig = ev.get("signal")
        if sig == "PLAN_COMPLETE":
            pending_plan += 1
        elif sig == "PLAN_APPROVED":
            if pending_plan <= 0:
                out.append(_violation("plan_approved_without_plan", "PLAN_APPROVED with no fresh PLAN_COMPLETE", i))
            else:
                pending_plan -= 1
            plan_approved = True
        elif sig == "PHASE_APPROVED":
            if not plan_approved:
                out.append(_violation("phase_before_plan", "PHASE_APPROVED before PLAN_APPROVED", i))
            phase_approved_seen = True
        elif sig in ("GO", "VERIFIED"):
            if not phase_approved_seen:
                out.append(_violation("final_without_phase", f"{sig} before any PHASE_APPROVED", i))
    return out


def check_no_skipped_review(events):
    """Every PHASE_APPROVED must be preceded by an IMPLEMENTATION_COMPLETE that
    hasn't already been "consumed" by an earlier approval — i.e. a reviewer
    actually reviewed an implementation rather than approving a phantom phase."""
    out = []
    pending_impl = 0
    for i, ev in enumerate(events):
        sig = ev.get("signal")
        if sig == "IMPLEMENTATION_COMPLETE":
            pending_impl += 1
        elif sig == "PHASE_APPROVED":
            if pending_impl <= 0:
                out.append(_violation("phase_approved_without_impl", "PHASE_APPROVED with no preceding IMPLEMENTATION_COMPLETE", i))
            else:
                pending_impl -= 1
    return out


def validate_trajectory(events):
    """Run all trajectory validators and return a combined list of violations.
    Empty list == the trajectory respects the protocol."""
    violations = []
    violations += check_signal_provenance(events)
    violations += check_pipeline_order(events)
    violations += check_no_skipped_review(events)
    return violations
