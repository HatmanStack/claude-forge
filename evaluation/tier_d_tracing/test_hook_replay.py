"""Tier D — replay a recorded hook-event sequence through the trace hook.

The event shapes mirror what Claude Code emits with Agent Teams on (captured
from a live run): the Agent tool's PostToolUse is a launch receipt
(`status: async_launched`), tools inside a subagent carry `agent_id`, a
SendMessage resume re-fires SubagentStart for the same agent, and each run
segment ends with SubagentStop. Spans go to an in-memory exporter; no network.
"""

import importlib.util
import io
import json
import stat
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk")
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "trace_subagents.py"
SID = "replay-session"


@pytest.fixture
def hook(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("forge_trace_hook_replay", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    mod._OTEL = {
        "trace": trace,
        "provider": provider,
        "tracer": provider.get_tracer("test"),
        "propagator": TraceContextTextMapPropagator(),
        "Status": Status,
        "StatusCode": StatusCode,
    }
    monkeypatch.setenv("CLAUDE_FORGE_TRACING", "1")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def fire(**payload):
        payload.setdefault("session_id", SID)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        with pytest.raises(SystemExit):
            mod.main()

    fire.spans = exporter.get_finished_spans
    fire.state_dir = tmp_path / "claude-forge-tracing" / SID
    return fire


def _transcript(tmp_path, name, input_tokens):
    p = tmp_path / f"{name}.jsonl"
    p.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2999-01-01T00:00:00Z",  # after any segment start
                "message": {"usage": {"input_tokens": input_tokens, "output_tokens": 7}},
            }
        )
        + "\n"
    )
    return str(p)


def _by_name(spans, prefix):
    return [s for s in spans if s.name.startswith(prefix)]


def _run_parallel_intake(fire, tmp_path):
    fire(hook_event_name="UserPromptSubmit", prompt="/repo-eval\nmore")
    for tu, role in (("tu1", "eval-hire"), ("tu2", "eval-stress")):
        fire(
            hook_event_name="PreToolUse",
            tool_name="Agent",
            tool_use_id=tu,
            tool_input={
                "subagent_type": f"forge:{role}",
                "name": role,
                "description": f"Eval {role}",
                "prompt": "score it",
            },
        )
    for tu, aid in (("tu1", "a1"), ("tu2", "a2")):
        fire(
            hook_event_name="PostToolUse",
            tool_name="Agent",
            tool_use_id=tu,
            tool_input={"name": "eval-hire" if aid == "a1" else "eval-stress"},
            tool_response={"isAsync": True, "status": "async_launched", "agentId": aid},
        )
    fire(hook_event_name="SubagentStart", agent_id="a1", agent_type="forge:eval-hire")
    fire(hook_event_name="SubagentStart", agent_id="a2", agent_type="forge:eval-stress")
    # Interleaved tool calls from the two parallel agents.
    fire(
        hook_event_name="PreToolUse",
        tool_name="Write",
        tool_use_id="w2",
        agent_id="a2",
        agent_type="forge:eval-stress",
        tool_input={"file_path": "b.md"},
    )
    fire(
        hook_event_name="PreToolUse",
        tool_name="Write",
        tool_use_id="w1",
        agent_id="a1",
        agent_type="forge:eval-hire",
        tool_input={"file_path": "a.md"},
    )
    fire(
        hook_event_name="PostToolUse",
        tool_name="Write",
        tool_use_id="w1",
        agent_id="a1",
        agent_type="forge:eval-hire",
        tool_response={"success": True},
    )
    fire(
        hook_event_name="PostToolUse",
        tool_name="Write",
        tool_use_id="w2",
        agent_id="a2",
        agent_type="forge:eval-stress",
        tool_response={"success": True},
    )
    # a1 reports properly; a2 stops without SendMessage(to="main").
    fire(
        hook_event_name="PreToolUse",
        tool_name="SendMessage",
        tool_use_id="s1",
        agent_id="a1",
        agent_type="forge:eval-hire",
        tool_input={"to": "main", "message": "Scores attached.\nEVAL_HIRE_COMPLETE"},
    )
    fire(
        hook_event_name="SubagentStop",
        agent_id="a1",
        agent_type="forge:eval-hire",
        agent_transcript_path=_transcript(tmp_path, "a1", 100),
        last_assistant_message="done",
    )
    fire(
        hook_event_name="SubagentStop",
        agent_id="a2",
        agent_type="forge:eval-stress",
        agent_transcript_path=_transcript(tmp_path, "a2", 50),
        last_assistant_message="EVAL_STRESS_COMPLETE",
    )


def test_parallel_agents_get_their_own_tool_spans(hook, tmp_path):
    _run_parallel_intake(hook, tmp_path)
    spans = hook.spans()
    anchors = {s.attributes["gen_ai.agent.id"]: s for s in _by_name(spans, "subagent:")}
    assert set(anchors) == {"a1", "a2"}
    (root,) = [s for s in spans if s.name.startswith("session:")]
    assert root.name == "session: /repo-eval"
    assert all(a.parent.span_id == root.context.span_id for a in anchors.values())
    assert anchors["a1"].attributes["gen_ai.agent.name"] == "eval-hire"
    assert anchors["a1"].attributes["forge.role"] == "eval-hire"
    for w in _by_name(spans, "tool:Write"):
        aid = w.attributes["gen_ai.agent.id"]
        assert w.parent.span_id == anchors[aid].context.span_id
        assert w.attributes["gen_ai.operation.name"] == "execute_tool"


def test_results_come_from_subagent_stop_not_the_launch_receipt(hook, tmp_path):
    _run_parallel_intake(hook, tmp_path)
    results = {
        s.attributes["gen_ai.agent.id"]: s for s in _by_name(hook.spans(), "subagent_result:")
    }
    assert set(results) == {"a1", "a2"}
    a1 = results["a1"].attributes
    assert a1["gen_ai.operation.name"] == "invoke_agent"
    assert a1["agent.reported"] is True
    assert "EVAL_HIRE_COMPLETE" in a1["agent.output"]
    assert "async_launched" not in a1["agent.output"]
    assert a1["gen_ai.usage.input_tokens"] == 100
    assert results["a2"].attributes["agent.reported"] is False


def test_resume_is_a_new_segment_on_the_same_anchor(hook, tmp_path):
    _run_parallel_intake(hook, tmp_path)
    hook(
        hook_event_name="PostToolUse",
        tool_name="SendMessage",
        tool_use_id="m1",
        tool_input={"to": "eval-hire", "message": "rescore"},
        tool_response={"success": True, "resumedAgentId": "a1"},
    )
    hook(hook_event_name="SubagentStart", agent_id="a1", agent_type="forge:eval-hire")
    hook(
        hook_event_name="SubagentStop",
        agent_id="a1",
        agent_type="forge:eval-hire",
        agent_transcript_path="",
        last_assistant_message="again",
    )
    spans = hook.spans()
    anchors = [s for s in _by_name(spans, "subagent:") if s.attributes["gen_ai.agent.id"] == "a1"]
    assert len(anchors) == 1
    msg = _by_name(spans, "message:")
    assert len(msg) == 1 and msg[0].parent.span_id == anchors[0].context.span_id
    segs = sorted(
        s.attributes["agent.segment"]
        for s in _by_name(spans, "subagent_result:")
        if s.attributes["gen_ai.agent.id"] == "a1"
    )
    assert segs == [1, 2]


def test_assessor_casting_a_gate_signal_is_flagged(hook, tmp_path):
    _run_parallel_intake(hook, tmp_path)
    hook(hook_event_name="SubagentStart", agent_id="a1", agent_type="forge:eval-hire")
    hook(
        hook_event_name="PreToolUse",
        tool_name="SendMessage",
        tool_use_id="s2",
        agent_id="a1",
        agent_type="forge:eval-hire",
        tool_input={"to": "main", "message": "All good.\nPHASE_APPROVED"},
    )
    hook(
        hook_event_name="SubagentStop",
        agent_id="a1",
        agent_type="forge:eval-hire",
        agent_transcript_path="",
        last_assistant_message="",
    )
    forged = _by_name(hook.spans(), "security:dp3.signal_forgery")
    assert len(forged) == 1 and forged[0].attributes["security.agent"] == "eval-hire"
    summary = json.loads((hook.state_dir / "trace-summary.json").read_text())
    assert {"role": "eval-hire", "signal": "PHASE_APPROVED"} in summary["events"]


def test_session_completes_at_session_end_covering_every_turn(hook, tmp_path):
    _run_parallel_intake(hook, tmp_path)
    hook(hook_event_name="Stop", background_tasks=[{"id": "a1", "type": "subagent"}])
    hook(hook_event_name="Stop", background_tasks=[])
    assert not _by_name(hook.spans(), "session_complete")  # Stop never ends a session
    hook(hook_event_name="SessionEnd")
    (done,) = _by_name(hook.spans(), "session_complete")
    assert done.status.status_code == StatusCode.OK


def test_a_failed_turn_marks_the_session_error(hook, tmp_path):
    _run_parallel_intake(hook, tmp_path)
    hook(hook_event_name="StopFailure")
    hook(hook_event_name="SessionEnd")
    (done,) = _by_name(hook.spans(), "session_complete")
    assert done.status.status_code == StatusCode.ERROR


def test_a_resumed_session_completes_again(hook, tmp_path):
    _run_parallel_intake(hook, tmp_path)
    hook(hook_event_name="SessionEnd")
    hook(hook_event_name="UserPromptSubmit", prompt="continue")
    hook(hook_event_name="SessionEnd")
    hook(hook_event_name="SessionEnd")
    assert len(_by_name(hook.spans(), "session_complete")) == 2
    # Two parallel assessors raise DP2 once per session, not once per completion.
    assert len(_by_name(hook.spans(), "security:dp2.shared_model_fanout")) == 1
    summary = json.loads((hook.state_dir / "trace-summary.json").read_text())
    assert summary["security"]["counts"].get("dp2") == 1

def test_session_state_is_private(hook, tmp_path):
    _run_parallel_intake(hook, tmp_path)
    assert stat.S_IMODE(hook.state_dir.stat().st_mode) == 0o700
    for f in hook.state_dir.iterdir():
        if f.is_file():
            assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


def test_workflow_structured_reports_are_captured(hook, tmp_path):
    """/forge:run agents report via StructuredOutput, not SendMessage."""
    hook(hook_event_name="UserPromptSubmit", prompt="/forge:run 2026-01-01-demo")
    hook(hook_event_name="SubagentStart", agent_id="w1", agent_type="forge:implementer")
    hook(hook_event_name="PreToolUse", tool_name="StructuredOutput", tool_use_id="so1",
         agent_id="w1", agent_type="forge:implementer",
         tool_input={"signal": "PHASE_APPROVED", "summary": "done"})
    hook(hook_event_name="SubagentStop", agent_id="w1", agent_type="forge:implementer",
         agent_transcript_path="", last_assistant_message="")
    (result,) = _by_name(hook.spans(), "subagent_result:")
    assert result.attributes["agent.reported"] is True
    assert result.attributes["forge.role"] == "implementer"
    # An implementer casting a reviewer's gate signal is a forged ballot.
    assert _by_name(hook.spans(), "security:dp3.signal_forgery")

