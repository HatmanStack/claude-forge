"""Tier C — trajectory validators against synthetic legal/illegal runs.

Deterministic per-PR coverage of the protocol invariants. The same validators
run against real `trace-summary.json` artifacts via check_run.py (nightly /
pre-release), where the events come from actual pipeline traces.
"""
from lib import trajectory as T


def ev(role, signal):
    return {"role": role, "signal": signal}


# A clean feature-flow run: plan, approve, implement, approve, ship.
LEGAL_FEATURE = [
    ev("planner", "PLAN_COMPLETE"),
    ev("plan-reviewer", "PLAN_APPROVED"),
    ev("implementer", "IMPLEMENTATION_COMPLETE"),
    ev("reviewer", "PHASE_APPROVED"),
    ev("implementer", "IMPLEMENTATION_COMPLETE"),
    ev("reviewer", "PHASE_APPROVED"),
    ev("final-reviewer", "GO"),
]

# A clean health-flow run (hygienist/fortifier as the implementer slot).
LEGAL_HEALTH = [
    ev("planner", "PLAN_COMPLETE"),
    ev("plan-reviewer", "PLAN_APPROVED"),
    ev("health-hygienist", "IMPLEMENTATION_COMPLETE"),
    ev("health-reviewer", "PHASE_APPROVED"),
    ev("reviewer", "VERIFIED"),
]


def test_legal_feature_trajectory_has_no_violations():
    assert T.validate_trajectory(LEGAL_FEATURE) == []


def test_legal_health_trajectory_has_no_violations():
    assert T.validate_trajectory(LEGAL_HEALTH) == []


def test_score_tool_trajectory_in_order():
    assert T.score_tool_trajectory_in_order(["a", "x", "b", "c"], ["a", "b", "c"]) == 1.0
    assert T.score_tool_trajectory_in_order(["b", "a", "c"], ["a", "b", "c"]) == 0.0
    assert T.score_tool_trajectory_in_order([], ["a"]) == 0.0


def test_dp3_forged_approval_by_generator_is_caught():
    """An implementer casting its own PHASE_APPROVED (forged ballot)."""
    run = [
        ev("planner", "PLAN_COMPLETE"),
        ev("plan-reviewer", "PLAN_APPROVED"),
        ev("implementer", "IMPLEMENTATION_COMPLETE"),
        ev("implementer", "PHASE_APPROVED"),  # forged
    ]
    kinds = {v["kind"] for v in T.validate_trajectory(run)}
    assert "signal_forgery" in kinds


def test_plan_approved_only_by_plan_reviewer():
    run = [ev("planner", "PLAN_COMPLETE"), ev("reviewer", "PLAN_APPROVED")]
    kinds = {v["kind"] for v in T.check_signal_provenance(run)}
    assert "signal_forgery" in kinds


def test_go_only_by_final_reviewer():
    run = [ev("reviewer", "GO")]
    kinds = {v["kind"] for v in T.check_signal_provenance(run)}
    assert "signal_forgery" in kinds


def test_phase_approved_before_plan_is_caught():
    run = [ev("reviewer", "PHASE_APPROVED")]
    kinds = {v["kind"] for v in T.check_pipeline_order(run)}
    assert "phase_before_plan" in kinds


def test_plan_approved_without_plan_complete_is_caught():
    run = [ev("plan-reviewer", "PLAN_APPROVED")]
    kinds = {v["kind"] for v in T.check_pipeline_order(run)}
    assert "plan_approved_without_plan" in kinds


def test_go_without_any_phase_is_caught():
    run = [
        ev("planner", "PLAN_COMPLETE"),
        ev("plan-reviewer", "PLAN_APPROVED"),
        ev("final-reviewer", "GO"),
    ]
    kinds = {v["kind"] for v in T.check_pipeline_order(run)}
    assert "final_without_phase" in kinds


def test_phase_approved_without_implementation_is_caught():
    """A reviewer approving a phantom phase (no implementation in between)."""
    run = [
        ev("planner", "PLAN_COMPLETE"),
        ev("plan-reviewer", "PLAN_APPROVED"),
        ev("reviewer", "PHASE_APPROVED"),  # no IMPLEMENTATION_COMPLETE before it
    ]
    kinds = {v["kind"] for v in T.check_no_skipped_review(run)}
    assert "phase_approved_without_impl" in kinds


def test_each_phase_consumes_one_implementation():
    """Two approvals require two implementations; the second is unbacked."""
    run = [
        ev("implementer", "IMPLEMENTATION_COMPLETE"),
        ev("reviewer", "PHASE_APPROVED"),
        ev("reviewer", "PHASE_APPROVED"),  # phantom
    ]
    kinds = {v["kind"] for v in T.check_no_skipped_review(run)}
    assert "phase_approved_without_impl" in kinds
