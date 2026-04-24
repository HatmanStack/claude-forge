#!/usr/bin/env python3
"""
Opt-in OpenTelemetry tracing hook for Claude Forge.

Wired to Claude Code hooks (see settings.local.json.example):
    - SessionStart            → opens per-session root anchor (canonical)
    - UserPromptSubmit        → opens root defensively if SessionStart missed it
    - PreToolUse  (.*)        → opens an anchor for subagent tools, records start for others
    - PostToolUse (.*)        → emits the span with real duration
    - PostToolUseFailure (.*) → same as PostToolUse but forces is_error=true
    - PermissionRequest (.*)  → emits permission_requested:<tool> span
    - PermissionDenied  (.*)  → emits permission_denied:<tool> span
    - PreCompact / PostCompact → emits compaction span (real duration)
    - InstructionsLoaded      → emits instructions.loaded span (CLAUDE.md / rules)
    - Stop                    → emits session_complete (idempotent — fires once)
    - StopFailure             → session_complete with is_error=true
    - SessionEnd              → session_complete (canonical end-of-session signal)

Spans are arranged hierarchically:
    session: <user prompt>
      └── subagent:<name>                    (anchor, ~0ms, parent for inner work)
            ├── tool:Read | tool:Edit | tool:Bash | ...   (real durations)
            └── subagent_result:<name>       (real duration, output + status)

A no-op unless CLAUDE_FORGE_TRACING=1. Silently exits if opentelemetry is missing
or the OTLP endpoint is unreachable. Never blocks a tool call.

Install:
    pip install opentelemetry-api opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-grpc

Enable:
    export CLAUDE_FORGE_TRACING=1
    # optional, defaults to http://localhost:4317
    export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
"""

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

SUBAGENT_TOOLS = ("Task", "Agent", "SendMessage")
PROMPT_LIMIT = 2048
OUTPUT_LIMIT = 2048
INPUT_LIMIT = 1024
FLUSH_TIMEOUT_MS = 1000  # force_flush budget per event; balance latency vs. drop risk


def _env_truthy(name):
    """Env-var truthiness with an explicit allowlist so `FOO=0` doesn't mean on."""
    val = (os.environ.get(name) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


# Debug log is opt-in (CLAUDE_FORGE_HOOK_DEBUG=1). Raw hook payloads contain
# prompts, tool inputs, and assistant output — writing them world-readable to
# /tmp is a data leak on multi-user systems. When enabled, we write to a
# per-user path with 0600 permissions.
DEBUG_LOG_ENABLED = _env_truthy("CLAUDE_FORGE_HOOK_DEBUG")
DEBUG_LOG = (
    os.environ.get("CLAUDE_FORGE_HOOK_DEBUG_LOG")
    or str(Path.home() / ".cache" / "claude-forge" / "hook.log")
)

# Mutational tools are traced by default — they're the signal of "what each
# subagent actually changed." Bash is intentionally excluded from the default
# set because a typical pipeline run invokes it hundreds of times (git, npm,
# tests, file inspection) and drowns out Write/Edit visibility. Add it back
# via CLAUDE_FORGE_TRACE_MUTATION_TOOLS if you need Bash spans.
#
# Override via CLAUDE_FORGE_TRACE_MUTATION_TOOLS (comma-separated list).
# Disable the whole category via CLAUDE_FORGE_TRACE_MUTATIONS=0.
_default_mutations = "Write,Edit,MultiEdit"
MUTATION_TOOLS = {
    s.strip()
    for s in os.environ.get("CLAUDE_FORGE_TRACE_MUTATION_TOOLS", _default_mutations).split(",")
    if s.strip()
}
TRACE_MUTATIONS = os.environ.get("CLAUDE_FORGE_TRACE_MUTATIONS", "1").strip().lower() not in (
    "0", "false", "no", "off", ""
)

# Inner tool tracing for *non-mutational* tools (Read, Glob, Grep, …) is opt-in:
# a normal /pipeline can fire 200+ such calls. Set CLAUDE_FORGE_TRACE_INNER=1.
TRACE_INNER = _env_truthy("CLAUDE_FORGE_TRACE_INNER")

# When inner tracing IS on, these read-only / planning tools are skipped by
# default to keep the trace tree readable. Override with a comma-separated list
# in CLAUDE_FORGE_TRACE_TOOL_BLOCKLIST (empty string disables the blocklist).
_default_blocklist = "Read,Glob,Grep,TodoWrite,NotebookRead"
INNER_TOOL_BLOCKLIST = {
    s.strip()
    for s in os.environ.get("CLAUDE_FORGE_TRACE_TOOL_BLOCKLIST", _default_blocklist).split(",")
    if s.strip()
}


def _exit_ok():
    sys.exit(0)


def _log(msg, raw=""):
    if not DEBUG_LOG_ENABLED:
        return
    try:
        p = Path(DEBUG_LOG)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Create with 0600 if new; chmod every write is fine, cheap.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(str(p), flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except Exception:
            pass
        with os.fdopen(fd, "a") as f:
            f.write(msg + "\n")
            if raw:
                f.write(raw[:500] + ("...\n" if len(raw) > 500 else "\n"))
    except Exception:
        pass


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(s, fallback_prefix="x"):
    """Filename-safe token. Sanitizes anything that could escape a Path segment."""
    if not isinstance(s, str) or not s:
        s = ""
    # Reject anything that tries path traversal or pure-dot sequences.
    cleaned = _SAFE_RE.sub("_", s)[:80]
    if not cleaned or cleaned in (".", ".."):
        # Unsafe or empty → deterministic hash fallback.
        h = hashlib.sha1((s or fallback_prefix).encode("utf-8")).hexdigest()[:16]
        return f"{fallback_prefix}_{h}"
    return cleaned


def _truncate(s, n):
    if not isinstance(s, str):
        s = str(s)
    return s if len(s) <= n else s[:n] + f"...[truncated {len(s) - n} chars]"


def _state_dir(session_id):
    # session_id comes from the hook payload; sanitize before using as a path
    # segment so a malformed value can't escape the tracing tmp root.
    safe = _safe_name(session_id, fallback_prefix="sess")
    d = Path(tempfile.gettempdir()) / "claude-forge-tracing" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key_for(tool_name, tool_input):
    """Stable correlation key across Pre/Post when no tool_use_id is provided."""
    src = json.dumps({"t": tool_name, "i": tool_input}, sort_keys=True, default=str)
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:16]


def _parse_ts(s):
    """Parse ISO timestamp from transcript to ns since epoch. Returns 0 on failure."""
    if not s:
        return 0
    try:
        # Handle trailing Z and fractional seconds.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return int(datetime.fromisoformat(s).timestamp() * 1_000_000_000)
    except Exception:
        return 0


def _sum_usage(transcript_path, since_ns=0, until_ns=0, sidechain_only=False):
    """Sum token usage across assistant lines in a transcript, optionally
    filtered by timestamp window and sidechain flag. Returns dict of totals
    plus a turn count. Silently returns zeros if the file is unreadable."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "turns": 0,
    }
    if not transcript_path:
        return totals
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") != "assistant":
                    continue
                if sidechain_only and not d.get("isSidechain"):
                    continue
                if since_ns or until_ns:
                    ts = _parse_ts(d.get("timestamp"))
                    if since_ns and ts and ts < since_ns:
                        continue
                    if until_ns and ts and ts > until_ns:
                        continue
                u = (d.get("message") or {}).get("usage") or {}
                for k in ("input_tokens", "output_tokens",
                         "cache_creation_input_tokens", "cache_read_input_tokens"):
                    totals[k] += int(u.get(k) or 0)
                totals["turns"] += 1
    except Exception:
        pass
    return totals


def _find_subagent_transcript(parent_transcript, description, start_ns, end_ns):
    """Locate the per-subagent transcript file for an Agent tool call.

    Claude Code writes each subagent's turns to:
        <parent_transcript_without_.jsonl>/subagents/agent-<id>.jsonl
    with a sibling agent-<id>.meta.json containing {agentType, description}.

    We match by description (Claude Forge sets distinct descriptions per role)
    and prefer the file whose mtime falls within the call's time window.
    Returns absolute path or "".
    """
    if not parent_transcript or not description:
        return ""
    base = parent_transcript[:-6] if parent_transcript.endswith(".jsonl") else parent_transcript
    sub_dir = Path(base) / "subagents"
    if not sub_dir.is_dir():
        return ""
    # SendMessage anchor names get a "(continued)" suffix from the naming
    # helper; meta.json descriptions don't have that suffix. Strip it before
    # matching so SendMessage events resolve back to the same transcript file
    # the original Agent spawn does.
    norm_desc = description.removesuffix(" (continued)").strip()
    # If the "description" we got is actually an agent_id (16-char hex), look
    # the meta file directly by name instead of scanning for descriptions.
    if len(norm_desc) >= 12 and all(c in "0123456789abcdef" for c in norm_desc.lower()):
        direct = sub_dir / f"agent-{norm_desc}.jsonl"
        if direct.exists():
            return str(direct)
    candidates = []
    try:
        for meta in sub_dir.glob("agent-*.meta.json"):
            try:
                m = json.loads(meta.read_text())
            except Exception:
                continue
            if m.get("description") != norm_desc:
                continue
            jsonl = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
            if jsonl.exists():
                candidates.append(jsonl)
    except Exception:
        return ""
    if not candidates:
        return ""
    if len(candidates) == 1:
        return str(candidates[0])
    # Multiple subagents shared this description (re-runs / multiple phases).
    # Prefer the one whose mtime sits inside the call window; otherwise newest.
    in_window = []
    for c in candidates:
        try:
            mtime_ns = int(c.stat().st_mtime * 1_000_000_000)
        except Exception:
            continue
        if (not start_ns or mtime_ns >= start_ns - 60_000_000_000) and \
           (not end_ns or mtime_ns <= end_ns + 60_000_000_000):
            in_window.append((mtime_ns, c))
    if in_window:
        in_window.sort()
        return str(in_window[-1][1])
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def _set_usage_attrs(span, prefix, usage):
    span.set_attribute(f"{prefix}.input_tokens", usage["input_tokens"])
    span.set_attribute(f"{prefix}.output_tokens", usage["output_tokens"])
    span.set_attribute(f"{prefix}.cache_creation_tokens", usage["cache_creation_input_tokens"])
    span.set_attribute(f"{prefix}.cache_read_tokens", usage["cache_read_input_tokens"])
    span.set_attribute(f"{prefix}.turns", usage["turns"])
    span.set_attribute(
        f"{prefix}.total_tokens",
        usage["input_tokens"] + usage["output_tokens"]
        + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"],
    )


def _otel():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    from opentelemetry.trace import Status, StatusCode

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    provider = TracerProvider(resource=Resource(attributes={"service.name": "claude-forge"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True, timeout=2))
    )
    return {
        "trace": trace,
        "provider": provider,
        "tracer": provider.get_tracer("claude-forge.subagents"),
        "propagator": TraceContextTextMapPropagator(),
        "Status": Status,
        "StatusCode": StatusCode,
    }


def _safe_flush(otel):
    """Bounded, exception-safe flush so a slow/unreachable OTLP endpoint can't
    block tool execution."""
    try:
        otel["provider"].force_flush(timeout_millis=FLUSH_TIMEOUT_MS)
    except Exception:
        pass


def _read_carrier(state_dir, fname):
    f = state_dir / fname
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def _emit_anchor(otel, name, parent_ctx, attrs, start_ns):
    """Emit a zero-duration span used purely as a parent for child spans."""
    span = otel["tracer"].start_span(name, context=parent_ctx, start_time=start_ns)
    for k, v in attrs.items():
        span.set_attribute(k, v)
    carrier = {}
    otel["propagator"].inject(carrier=carrier, context=otel["trace"].set_span_in_context(span))
    span.end(end_time=start_ns + 1)
    return carrier


# ---------------- handlers ----------------

def _ensure_root(otel, state_dir, session_id, prompt="", cwd="", transcript_path=""):
    """Create a root anchor span if one doesn't exist yet for this session.
    Reuses an existing _root.json so the trace_id stays stable across the
    whole pipeline. Called from UserPromptSubmit and (defensively) from
    subagent Pre when no root has been created yet."""
    root_file = state_dir / "_root.json"
    if root_file.exists():
        return  # already anchored — keep the same trace_id
    start_ns = time.time_ns()
    name = f"session: {prompt.splitlines()[0][:80]}" if prompt else f"session: {session_id[:8]}"
    carrier = _emit_anchor(
        otel,
        name,
        parent_ctx=None,
        attrs={
            "session.id": session_id,
            "session.cwd": cwd,
            "user.prompt": _truncate(prompt, PROMPT_LIMIT),
        },
        start_ns=start_ns,
    )
    root_file.write_text(json.dumps({
        "carrier": carrier,
        "start_ns": start_ns,
        "prompt": _truncate(prompt, PROMPT_LIMIT),
        "cwd": cwd,
        "transcript_path": transcript_path,
    }))
    _log(f"root anchor created for session={session_id[:8]} name={name!r}")


def _handle_user_prompt(otel, payload, state_dir):
    # Idempotent: if a root already exists for this session, keep it so the
    # whole pipeline shares one trace_id. (Some setups fire UserPromptSubmit
    # for nested prompting; we don't want to fork the trace.)
    _ensure_root(
        otel, state_dir,
        session_id=payload.get("session_id") or "default",
        prompt=payload.get("prompt") or "",
        cwd=payload.get("cwd") or "",
        transcript_path=payload.get("transcript_path") or "",
    )


def _emit_session_complete(otel, payload, state_dir, is_error=False):
    """Emit the session_complete span. Idempotent: marks `complete_emitted` in
    _root.json so multiple triggers (Stop fires per-turn, SessionEnd once at
    close, StopFailure on API error) collapse to a single span per session."""
    root = _read_carrier(state_dir, "_root.json")
    if not root:
        return
    if root.get("complete_emitted"):
        # Update error status if a later signal escalates from clean → error.
        if is_error and not root.get("complete_error"):
            root["complete_error"] = True
            try:
                (state_dir / "_root.json").write_text(json.dumps(root))
            except Exception:
                pass
        return
    end_ns = time.time_ns()
    start_ns = root.get("start_ns") or end_ns
    parent_ctx = otel["propagator"].extract(carrier=root.get("carrier") or {})
    span = otel["tracer"].start_span(
        "session_complete", context=parent_ctx, start_time=start_ns
    )
    span.set_attribute("session.id", payload.get("session_id") or "default")
    span.set_attribute("session.duration_ms", (end_ns - start_ns) // 1_000_000)
    span.set_attribute("user.prompt", root.get("prompt", ""))
    span.set_attribute("session.cwd", root.get("cwd", ""))
    span.set_attribute("session.is_error", is_error)
    transcript = payload.get("transcript_path") or root.get("transcript_path") or ""
    if transcript:
        span.set_attribute("session.transcript_path", transcript)
        totals = _sum_usage(transcript)
        base = transcript[:-6] if transcript.endswith(".jsonl") else transcript
        sub_dir = Path(base) / "subagents"
        if sub_dir.is_dir():
            for jsonl in sub_dir.glob("agent-*.jsonl"):
                u = _sum_usage(str(jsonl))
                for k in totals:
                    totals[k] += u[k]
        _set_usage_attrs(span, "session.tokens", totals)
    if is_error:
        span.set_status(otel["Status"](otel["StatusCode"].ERROR, "session ended in failure"))
    else:
        span.set_status(otel["Status"](otel["StatusCode"].OK))
    span.end(end_time=end_ns)
    _safe_flush(otel)
    # Mark complete; keep _root.json so subagent calls after a Stop can still
    # parent under the same trace_id. State accumulates in /tmp but the OS reaps it.
    try:
        root["complete_emitted"] = True
        root["complete_error"] = bool(is_error)
        root["stopped_at_ns"] = end_ns
        (state_dir / "_root.json").write_text(json.dumps(root))
    except Exception:
        pass


def _handle_stop(otel, payload, state_dir):
    """Stop fires after every Claude turn — defer to the idempotent emitter,
    which only fires session_complete once per session."""
    _emit_session_complete(otel, payload, state_dir)


def _handle_session_end(otel, payload, state_dir):
    """SessionEnd is the canonical end-of-session signal."""
    _emit_session_complete(otel, payload, state_dir)


def _handle_stop_failure(otel, payload, state_dir):
    """StopFailure: turn ended due to API error. Mark session_complete as error."""
    _emit_session_complete(otel, payload, state_dir, is_error=True)


def _handle_session_start(otel, payload, state_dir):
    """SessionStart is the cleanest root-anchor trigger. UserPromptSubmit also
    creates the root defensively (idempotent), so resumed sessions still anchor
    correctly even if SessionStart didn't fire."""
    _ensure_root(
        otel, state_dir,
        session_id=payload.get("session_id") or "default",
        prompt=payload.get("prompt") or "",
        cwd=payload.get("cwd") or "",
        transcript_path=payload.get("transcript_path") or "",
    )


def _parent_ctx(otel, state_dir):
    """Pick the most appropriate parent context: active subagent if one is
    running, else the session root, else None."""
    cur = _read_carrier(state_dir, "_current_agent.json")
    if cur and cur.get("carrier"):
        return otel["propagator"].extract(carrier=cur["carrier"])
    root = _read_carrier(state_dir, "_root.json")
    if root and root.get("carrier"):
        return otel["propagator"].extract(carrier=root["carrier"])
    return None


def _handle_permission(otel, payload, state_dir, denied):
    """Emit a span for permission events so Jaeger shows when a tool was asked
    to be approved or was blocked. Parents to the active subagent if any."""
    parent_ctx = _parent_ctx(otel, state_dir)
    if parent_ctx is None:
        return
    now_ns = time.time_ns()
    tool = payload.get("tool_name") or payload.get("toolName") or "?"
    op = "permission_denied" if denied else "permission_requested"
    span = otel["tracer"].start_span(f"{op}:{tool}", context=parent_ctx, start_time=now_ns)
    span.set_attribute("permission.tool", tool)
    span.set_attribute("permission.denied", denied)
    reason = payload.get("reason") or payload.get("message") or ""
    if reason:
        span.set_attribute("permission.reason", _truncate(reason, INPUT_LIMIT))
    if denied:
        span.set_status(otel["Status"](otel["StatusCode"].ERROR, "permission denied"))
    span.end(end_time=now_ns + 1)
    _safe_flush(otel)


def _handle_pre_compact(otel, payload, state_dir):
    """Stash compaction start time; PostCompact emits the span."""
    try:
        (state_dir / "_compaction.json").write_text(json.dumps({"start_ns": time.time_ns()}))
    except Exception:
        pass


def _handle_post_compact(otel, payload, state_dir):
    f = state_dir / "_compaction.json"
    end_ns = time.time_ns()
    start_ns = end_ns
    if f.exists():
        try:
            d = json.loads(f.read_text())
            start_ns = d.get("start_ns") or end_ns
        except Exception:
            pass
    parent_ctx = _parent_ctx(otel, state_dir)
    if parent_ctx is None:
        return
    span = otel["tracer"].start_span("compaction", context=parent_ctx, start_time=start_ns)
    span.set_attribute("compaction.duration_ms", (end_ns - start_ns) // 1_000_000)
    span.end(end_time=end_ns)
    _safe_flush(otel)
    try:
        f.unlink()
    except Exception:
        pass


def _handle_instructions_loaded(otel, payload, state_dir):
    """Annotate the trace when CLAUDE.md or rule files load — useful when an
    instruction file shapes agent behavior unexpectedly."""
    parent_ctx = _parent_ctx(otel, state_dir)
    if parent_ctx is None:
        return
    now_ns = time.time_ns()
    path = (
        payload.get("path") or payload.get("file_path")
        or payload.get("instructionFile") or payload.get("file") or "?"
    )
    inst_type = payload.get("type") or payload.get("instructionType") or ""
    span = otel["tracer"].start_span(
        "instructions.loaded", context=parent_ctx, start_time=now_ns
    )
    span.set_attribute("instructions.path", str(path))
    if inst_type:
        span.set_attribute("instructions.type", str(inst_type))
    span.end(end_time=now_ns + 1)
    _safe_flush(otel)


def _lookup_agent_id_in_meta(transcript_path, agent_id):
    """Resolve an agent_id to its description by reading the per-subagent
    meta.json that Claude Code writes alongside each subagent's transcript:
        <session>/subagents/agent-<id>.meta.json
    Returns description string or "" on miss.
    """
    if not transcript_path or not agent_id:
        return ""
    base = transcript_path[:-6] if transcript_path.endswith(".jsonl") else transcript_path
    meta = Path(base) / "subagents" / f"agent-{agent_id}.meta.json"
    if not meta.exists():
        return ""
    try:
        d = json.loads(meta.read_text())
        return d.get("description") or ""
    except Exception:
        return ""


def _agent_name_from_payload(tool_input, tool_name, state_dir, transcript_path=""):
    """Pick the most descriptive label for a span.

    - Agent payloads have a `description` ("Planner: unified audit remediation
      plan") and `name` ("planner"). Prefer description.
    - SendMessage payloads have `to` (role-name OR agent-id) and `summary`.
      Resolution order: saved name map → meta.json on disk (for agent-ids) →
      fall back to the raw `to` value.
    """
    if tool_name == "SendMessage":
        to = (tool_input.get("to") or tool_input.get("recipient") or "").strip()
        if to:
            mapped = _read_carrier(state_dir, "_agent_names.json") or {}
            name = mapped.get(to) or mapped.get(to.lower())
            if name:
                return f"{name} (continued)"
            # Agent-id form ("a987f5c0d71bc551c") — agent_ids aren't in the
            # Agent tool's response, so the saved name map only has role
            # entries. Fall back to the per-subagent meta.json that Claude
            # Code writes alongside each subagent's transcript.
            desc = _lookup_agent_id_in_meta(transcript_path, to)
            if desc:
                # Cache for next time so subsequent SendMessages by the same
                # id don't re-read the file.
                try:
                    f = state_dir / "_agent_names.json"
                    cur = json.loads(f.read_text()) if f.exists() else {}
                    cur[to] = desc
                    f.write_text(json.dumps(cur))
                except Exception:
                    pass
                return f"{desc} (continued)"
            return to + " (continued)"
        return tool_input.get("summary") or "send_message"
    return (
        tool_input.get("description")
        or tool_input.get("subagent_type")
        or "subagent"
    )


def _record_agent_name(state_dir, tool_input):
    """Save name+description+id mappings so future SendMessage Pre events can
    resolve `to=<role>` or `to=<agent_id>` back to a friendly name."""
    f = state_dir / "_agent_names.json"
    try:
        existing = json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        existing = {}
    desc = tool_input.get("description") or ""
    role = tool_input.get("name") or ""
    if desc and role:
        existing[role] = desc
        existing[role.lower()] = desc
    f.write_text(json.dumps(existing))


def _handle_subagent_pre(otel, payload, state_dir, tool_use_id):
    """Emit an anchor span for the subagent so child tool calls have a parent.
    Save the anchor's carrier for child lookup, plus start time + input for the
    real-duration result span emitted at Post."""
    tool_input = payload.get("tool_input") or {}
    tool_name = payload.get("tool_name") or ""
    # Save role-name → description so SendMessage Pre can resolve to that.
    if tool_name == "Agent":
        _record_agent_name(state_dir, tool_input)
    name = _agent_name_from_payload(
        tool_input, tool_name, state_dir,
        transcript_path=payload.get("transcript_path") or "",
    )
    start_ns = time.time_ns()
    # Defensive: if UserPromptSubmit didn't fire (or fired with a different
    # session_id), create a root anchor now so this subagent — and all that
    # follow — share one trace instead of becoming separate root traces.
    _ensure_root(
        otel, state_dir,
        session_id=payload.get("session_id") or "default",
        cwd=payload.get("cwd") or "",
        transcript_path=payload.get("transcript_path") or "",
    )
    root = _read_carrier(state_dir, "_root.json")
    parent_ctx = (
        otel["propagator"].extract(carrier=root.get("carrier") or {}) if root else None
    )
    # Build attrs that work for both Agent (description/prompt) and
    # SendMessage (to/summary/message) payload shapes.
    attrs = {
        "agent.subagent_type": tool_input.get("subagent_type", ""),
        "agent.description": tool_input.get("description", ""),
        "agent.name": tool_input.get("name", ""),
        "agent.tool_name": tool_name,
        "session.id": payload.get("session_id") or "default",
    }
    if tool_name == "SendMessage":
        attrs["sendmessage.to"] = tool_input.get("to") or tool_input.get("recipient", "")
        attrs["sendmessage.summary"] = tool_input.get("summary", "")
        attrs["agent.prompt"] = _truncate(
            tool_input.get("message") or tool_input.get("content") or "", PROMPT_LIMIT
        )
    else:
        attrs["agent.prompt"] = _truncate(tool_input.get("prompt") or "", PROMPT_LIMIT)
    carrier = _emit_anchor(
        otel,
        f"subagent:{name}",
        parent_ctx=parent_ctx,
        attrs=attrs,
        start_ns=start_ns,
    )
    state = {
        "carrier": carrier,
        "name": name,
        "start_ns": start_ns,
        "tool_input": tool_input,
        "tool_name": payload.get("tool_name") or "",
        "transcript_path": payload.get("transcript_path") or "",
    }
    (state_dir / f"agent_{tool_use_id}.json").write_text(json.dumps(state))
    # _current_agent.json is single-writer because Claude Forge's
    # pipeline-protocol.md mandates strictly sequential subagent spawning
    # ("NO parallel agents"). If a future flow ever runs subagents concurrently,
    # inner-tool spans here could parent to the wrong subagent — switch to a
    # per-invocation key + skip-on-ambiguity at that point.
    (state_dir / "_current_agent.json").write_text(json.dumps(state))
    _safe_flush(otel)


def _emit_subagent_inner_spans(otel, transcript_path, parent_carrier, agent_name,
                               anchor_start_ns, anchor_end_ns):
    """Synthesize tool:<name> spans from a subagent's per-agent JSONL transcript.

    Workaround for Claude Code issue #34692 (subagent tool calls don't fire
    the parent's PreToolUse/PostToolUse hooks). The subagent's own transcript
    records every tool_use + tool_result with timestamps; we walk it after
    the subagent finishes and emit retroactive spans parented to the subagent
    anchor so the trace tree shows what each subagent actually did.

    Honors the same gates as live inner-tool tracing (_should_trace_inner):
    mutational tools (Write/Edit/MultiEdit/Bash) by default, others require
    CLAUDE_FORGE_TRACE_INNER=1, INNER_TOOL_BLOCKLIST drops Read/Glob/Grep/etc.
    """
    if not transcript_path:
        return
    p = Path(transcript_path)
    if not p.exists():
        return

    parent_ctx = otel["propagator"].extract(carrier=parent_carrier or {})
    tool_uses = {}      # tool_use_id -> {name, input, ts_ns}
    tool_results = {}   # tool_use_id -> {content, is_error, ts_ns}

    try:
        with p.open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ts_ns = _parse_ts(d.get("timestamp"))
                msg = d.get("message") or {}
                if d.get("type") == "assistant":
                    for c in (msg.get("content") or []):
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            tid = c.get("id") or ""
                            if tid:
                                tool_uses[tid] = {
                                    "name": c.get("name") or "?",
                                    "input": c.get("input") or {},
                                    "ts_ns": ts_ns,
                                }
                elif d.get("type") == "user":
                    for c in (msg.get("content") or []):
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            tid = c.get("tool_use_id") or ""
                            if tid:
                                tool_results[tid] = {
                                    "content": c.get("content"),
                                    "is_error": bool(c.get("is_error")),
                                    "ts_ns": ts_ns,
                                }
    except Exception:
        return

    # Skip tool_uses with timestamps before anchor_start_ns. This matters for
    # SendMessage continuations: the same per-subagent transcript is shared
    # across the original Agent spawn + every SendMessage to that agent. Each
    # post-event runs synth and we'd re-emit the same spans without this guard.
    # anchor_start_ns is the SendMessage's Pre time, so only tool_uses that
    # happened during this SendMessage window get emitted.
    emitted = 0
    for tid, use in tool_uses.items():
        tool_name = use["name"]
        if not _should_trace_inner(tool_name):
            continue
        result = tool_results.get(tid, {})
        start_ns = use.get("ts_ns") or anchor_start_ns
        end_ns = result.get("ts_ns") or anchor_end_ns
        if not end_ns or end_ns < start_ns:
            end_ns = start_ns + 1
        # Only emit tool spans that fall within this anchor's window
        if anchor_start_ns and start_ns < anchor_start_ns:
            continue

        span = otel["tracer"].start_span(
            f"tool:{tool_name}", context=parent_ctx, start_time=start_ns
        )
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("tool.duration_ms", (end_ns - start_ns) // 1_000_000)
        span.set_attribute("agent.name", agent_name or "")
        try:
            span.set_attribute(
                "tool.input",
                _truncate(json.dumps(use.get("input") or {}, default=str), INPUT_LIMIT),
            )
        except Exception:
            pass
        is_error = bool(result.get("is_error"))
        span.set_attribute("tool.is_error", is_error)
        content = result.get("content")
        if isinstance(content, list):
            content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
        if isinstance(content, str):
            span.set_attribute("tool.output", _truncate(content, OUTPUT_LIMIT))
        if is_error:
            span.set_status(otel["Status"](otel["StatusCode"].ERROR, "tool reported error"))
        span.end(end_time=end_ns)
        emitted += 1

    if emitted:
        _safe_flush(otel)


def _handle_subagent_post(otel, payload, state_dir, tool_use_id):
    state_file = state_dir / f"agent_{tool_use_id}.json"
    if not state_file.exists():
        return
    saved = json.loads(state_file.read_text())
    end_ns = time.time_ns()
    start_ns = saved.get("start_ns") or end_ns
    parent_ctx = otel["propagator"].extract(carrier=saved.get("carrier") or {})

    name = saved.get("name") or "subagent"
    span = otel["tracer"].start_span(
        f"subagent_result:{name}", context=parent_ctx, start_time=start_ns
    )
    span.set_attribute("agent.duration_ms", (end_ns - start_ns) // 1_000_000)
    tool_response = payload.get("tool_response") or {}
    is_error = bool(tool_response.get("isError") or tool_response.get("is_error"))
    span.set_attribute("agent.is_error", is_error)
    content = tool_response.get("content")
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    if isinstance(content, str):
        span.set_attribute("agent.output", _truncate(content, OUTPUT_LIMIT))
    if is_error:
        span.set_status(otel["Status"](otel["StatusCode"].ERROR, "subagent reported error"))
    else:
        span.set_status(otel["Status"](otel["StatusCode"].OK))

    transcript = (
        payload.get("transcript_path")
        or saved.get("transcript_path")
        or ""
    )
    sub_jsonl = ""
    if transcript:
        # Each subagent has its own JSONL under <session>/subagents/. Locate it
        # by matching description, then sum its assistant usage. Falls back to
        # the parent transcript's window if the subagent file isn't found.
        sub_jsonl = _find_subagent_transcript(
            transcript, saved.get("name") or "", start_ns, end_ns
        )
        if sub_jsonl:
            usage = _sum_usage(sub_jsonl)
            span.set_attribute("agent.subagent_transcript", sub_jsonl)
        else:
            usage = _sum_usage(transcript, since_ns=start_ns, until_ns=end_ns)
        _set_usage_attrs(span, "agent.tokens", usage)

    span.end(end_time=end_ns)

    # After the result span is sealed, retroactively emit tool:<name> spans
    # for every tool the subagent invoked internally — Claude Code's hook
    # subsystem doesn't fire for subagent tools (issues #34692/#18392), so
    # we synthesize them from the subagent's own JSONL transcript.
    if sub_jsonl:
        _emit_subagent_inner_spans(
            otel,
            transcript_path=sub_jsonl,
            parent_carrier=saved.get("carrier") or {},
            agent_name=saved.get("name") or "",
            anchor_start_ns=start_ns,
            anchor_end_ns=end_ns,
        )

    _safe_flush(otel)

    try:
        state_file.unlink()
    except Exception:
        pass
    cur = state_dir / "_current_agent.json"
    try:
        if cur.exists():
            data = json.loads(cur.read_text())
            if data.get("carrier") == saved.get("carrier"):
                cur.unlink()
    except Exception:
        pass


def _should_trace_inner(tool_name):
    """Mutational tools trace by default; everything else needs TRACE_INNER=1
    and isn't in the blocklist."""
    if tool_name in MUTATION_TOOLS:
        return TRACE_MUTATIONS
    return TRACE_INNER and tool_name not in INNER_TOOL_BLOCKLIST


def _handle_inner_pre(payload, state_dir, key):
    """Record start time + input for a non-subagent tool call happening inside
    an active subagent. No-op if the tool isn't being traced or there's no
    active agent."""
    if not _should_trace_inner(payload.get("tool_name") or ""):
        return
    if not (state_dir / "_current_agent.json").exists():
        return
    tool_input = payload.get("tool_input") or {}
    (state_dir / f"tool_{key}.json").write_text(json.dumps({
        "start_ns": time.time_ns(),
        "tool_name": payload.get("tool_name") or "",
        "tool_input": tool_input,
    }))


def _handle_inner_post(otel, payload, state_dir, key):
    if not _should_trace_inner(payload.get("tool_name") or ""):
        return
    state_file = state_dir / f"tool_{key}.json"
    if not state_file.exists():
        return
    cur = _read_carrier(state_dir, "_current_agent.json")
    if not cur:
        try: state_file.unlink()
        except Exception: pass
        return
    try:
        saved = json.loads(state_file.read_text())
    except Exception:
        return
    end_ns = time.time_ns()
    start_ns = saved.get("start_ns") or end_ns
    parent_ctx = otel["propagator"].extract(carrier=cur.get("carrier") or {})
    tool_name = saved.get("tool_name") or "tool"
    span = otel["tracer"].start_span(
        f"tool:{tool_name}", context=parent_ctx, start_time=start_ns
    )
    span.set_attribute("tool.name", tool_name)
    span.set_attribute("tool.duration_ms", (end_ns - start_ns) // 1_000_000)
    span.set_attribute("agent.name", cur.get("name", ""))
    try:
        span.set_attribute(
            "tool.input",
            _truncate(json.dumps(saved.get("tool_input") or {}, default=str), INPUT_LIMIT),
        )
    except Exception:
        pass
    tool_response = payload.get("tool_response") or {}
    is_error = bool(tool_response.get("isError") or tool_response.get("is_error"))
    span.set_attribute("tool.is_error", is_error)
    content = tool_response.get("content")
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    if isinstance(content, str):
        span.set_attribute("tool.output", _truncate(content, OUTPUT_LIMIT))
    if is_error:
        span.set_status(otel["Status"](otel["StatusCode"].ERROR, "tool reported error"))
    span.end(end_time=end_ns)
    _safe_flush(otel)
    try:
        state_file.unlink()
    except Exception:
        pass


# ---------------- entry point ----------------

def main():
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    _log(
        f"called tracing={os.environ.get('CLAUDE_FORGE_TRACING','UNSET')} bytes={len(raw)}",
        raw,
    )

    if not _env_truthy("CLAUDE_FORGE_TRACING"):
        _exit_ok()

    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        _exit_ok()

    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    session_id = payload.get("session_id") or payload.get("sessionId") or "default"

    try:
        otel = _otel()
        state_dir = _state_dir(session_id)
    except Exception:
        _exit_ok()

    try:
        if event in ("UserPromptSubmit", "user_prompt_submit"):
            _handle_user_prompt(otel, payload, state_dir)
        elif event in ("SessionStart", "session_start"):
            _handle_session_start(otel, payload, state_dir)
        elif event in ("SessionEnd", "session_end"):
            _handle_session_end(otel, payload, state_dir)
        elif event in ("Stop", "stop"):
            _handle_stop(otel, payload, state_dir)
        elif event in ("StopFailure", "stop_failure"):
            _handle_stop_failure(otel, payload, state_dir)
        elif event in ("PermissionDenied", "permission_denied"):
            _handle_permission(otel, payload, state_dir, denied=True)
        elif event in ("PermissionRequest", "permission_request"):
            _handle_permission(otel, payload, state_dir, denied=False)
        elif event in ("PreCompact", "pre_compact"):
            _handle_pre_compact(otel, payload, state_dir)
        elif event in ("PostCompact", "post_compact"):
            _handle_post_compact(otel, payload, state_dir)
        elif event in ("InstructionsLoaded", "instructions_loaded"):
            _handle_instructions_loaded(otel, payload, state_dir)
        elif event in ("PreToolUse", "pre_tool_use",
                       "PostToolUse", "post_tool_use",
                       "PostToolUseFailure", "post_tool_use_failure"):
            tool_name = payload.get("tool_name") or payload.get("toolName") or ""
            tool_input = payload.get("tool_input") or {}
            raw_tool_use_id = (
                payload.get("tool_use_id")
                or payload.get("toolUseId")
                or tool_input.get("id")
                or _key_for(tool_name, tool_input)
            )
            # tool_use_id becomes a filename (agent_<id>.json / tool_<id>.json),
            # so strip anything that could escape the state dir.
            tool_use_id = _safe_name(raw_tool_use_id, fallback_prefix="tu")
            is_subagent = tool_name in SUBAGENT_TOOLS
            is_failure = event in ("PostToolUseFailure", "post_tool_use_failure")
            if event in ("PreToolUse", "pre_tool_use"):
                if is_subagent:
                    _handle_subagent_pre(otel, payload, state_dir, tool_use_id)
                else:
                    _handle_inner_pre(payload, state_dir, tool_use_id)
            else:
                # PostToolUseFailure carries the error tool_result. Force the
                # is_error attribute so the span status reflects the failure
                # even when the payload doesn't set isError explicitly.
                if is_failure:
                    tr = payload.setdefault("tool_response", {})
                    if isinstance(tr, dict):
                        tr.setdefault("isError", True)
                if is_subagent:
                    _handle_subagent_post(otel, payload, state_dir, tool_use_id)
                else:
                    _handle_inner_post(otel, payload, state_dir, tool_use_id)
    except Exception as e:
        _log(f"handler error event={event}: {e!r}")

    _exit_ok()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
