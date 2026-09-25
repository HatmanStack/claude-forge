#!/usr/bin/env python3
"""
Opt-in OpenTelemetry tracing hook for Claude Forge.

Wired to Claude Code hooks by bin/install-tracing.sh (see also
.claude/settings.local.json.example):
    - UserPromptSubmit                 → per-session root anchor, named after the
                                         prompt (idempotent)
    - PreToolUse  Agent (main thread)  → records spawn metadata (name, prompt)
    - PostToolUse Agent (main thread)  → binds that metadata to the returned agentId
    - SubagentStart                    → opens the subagent:<name> anchor on first
                                         start; opens a new run segment on resume
    - Pre/PostToolUse inside a subagent (payload carries `agent_id`)
                                       → tool:<name> spans parented to that agent;
                                         reports are captured from SendMessage(to="main")
                                         (skills) and StructuredOutput (/forge:run)
    - PostToolUse SendMessage (main)   → message:<name> span (orchestrator continuation)
    - SubagentStop                     → subagent_result:<name> for the run segment,
                                         token usage, security analysis
    - PermissionRequest / PermissionDenied → permission_{requested,denied}:<tool>
    - PreCompact / PostCompact         → compaction span (real duration)
    - InstructionsLoaded               → instructions.loaded span
    - StopFailure                      → marks the session errored
    - SessionEnd                       → session_complete, covering every turn
                                         (re-armed if the session is resumed)

Spans are arranged hierarchically:
    session: <user prompt>
      ├── subagent:<name>                (anchor, ~0ms, one per agent_id)
      │     ├── tool:Write | tool:Edit | ...   (real durations)
      │     ├── message:<name>           (orchestrator SendMessage continuation)
      │     └── subagent_result:<name>   (one per run segment: spawn or resume)
      └── session_complete

Agents are attributed by the `agent_id` Claude Code puts on every hook fired
inside a subagent, so parallel agents (the /repo-eval and /audit evaluator
fan-out) never share a parent. Completion comes from SubagentStop: with Agent
Teams on, the Agent tool returns at spawn (`status: async_launched`), so its
PostToolUse is a launch receipt, not a result.

Semantic conventions: agent and tool spans carry the OpenTelemetry GenAI
attributes (`gen_ai.operation.name`, `gen_ai.agent.*`, `gen_ai.tool.*`,
`gen_ai.usage.*`) so agent-aware backends recognise them. Forge-specific
detail stays under `agent.*`, `tool.*`, `session.*`, `security.*`, `forge.*`.

A no-op unless CLAUDE_FORGE_TRACING=1. Silently exits if opentelemetry is missing
or the OTLP endpoint is unreachable. Never blocks a tool call.

Install:
    pip install opentelemetry-api opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-grpc

Enable:
    export CLAUDE_FORGE_TRACING=1
    # optional; standard OTLP env vars apply (endpoint, headers, TLS).
    # Defaults to http://localhost:4317 (plaintext only for http:// endpoints).
    export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
"""

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no advisory locks; hooks still run, just unserialized
    fcntl = None

PROMPT_LIMIT = 2048
OUTPUT_LIMIT = 2048
INPUT_LIMIT = 1024
FLUSH_TIMEOUT_MS = 1000  # one force_flush per hook process; bounds latency vs. drop risk


def _env_truthy(name):
    """Env-var truthiness with an explicit allowlist so `FOO=0` doesn't mean on."""
    val = (os.environ.get(name) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


# Debug log is opt-in (CLAUDE_FORGE_HOOK_DEBUG=1). Raw hook payloads contain
# prompts, tool inputs, and assistant output — writing them world-readable to
# /tmp is a data leak on multi-user systems. When enabled, we write to a
# per-user path with 0600 permissions.
DEBUG_LOG_ENABLED = _env_truthy("CLAUDE_FORGE_HOOK_DEBUG")
DEBUG_LOG = os.environ.get("CLAUDE_FORGE_HOOK_DEBUG_LOG") or str(
    Path.home() / ".cache" / "claude-forge" / "hook.log"
)

# Mutational tools are traced by default — they're the signal of "what each
# subagent actually changed." Bash is intentionally excluded from the default
# set because a typical pipeline run invokes it hundreds of times (git, npm,
# tests, file inspection) and drowns out Write/Edit visibility. Add it back
# via CLAUDE_FORGE_TRACE_MUTATION_TOOLS if you need Bash spans (and widen the
# hook matcher: bin/install-tracing.sh --all-tools).
#
# Override via CLAUDE_FORGE_TRACE_MUTATION_TOOLS (comma-separated list).
# Disable the whole category via CLAUDE_FORGE_TRACE_MUTATIONS=0.
_default_mutations = "Write,Edit,MultiEdit,NotebookEdit"
MUTATION_TOOLS = {
    s.strip()
    for s in os.environ.get("CLAUDE_FORGE_TRACE_MUTATION_TOOLS", _default_mutations).split(",")
    if s.strip()
}
TRACE_MUTATIONS = os.environ.get("CLAUDE_FORGE_TRACE_MUTATIONS", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
    "",
)

# Tracing of *non-mutational* tools (Read, Glob, Grep, …) is opt-in: a normal
# /pipeline can fire 200+ such calls. Set CLAUDE_FORGE_TRACE_INNER=1 and install
# with --all-tools so the hook matcher sees them.
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

# ---------------- security analysis (defense-in-depth tracing) ----------------
# Passive detection layer. The hook reads each subagent's ROLE from the
# `agent_type` Claude Code stamps on its hook events and its ACTIONS from its own
# transcript — which attacker-controlled file/text content cannot forge — so it
# can attest signal provenance (DP3) and re-derive governance out-of-band (DP5),
# the two things the in-band channel/aggregator cannot do for themselves.
# Findings surface as `security:dp{1..5}.*` spans (status ERROR for visibility)
# plus a per-session summary on session_complete.
# DETECTION ONLY: it never blocks a tool call and never changes pipeline flow.
# Tuned for first-party repos (low false positives). On by default when tracing
# is on; disable with CLAUDE_FORGE_TRACE_SECURITY=0.
TRACE_SECURITY = os.environ.get("CLAUDE_FORGE_TRACE_SECURITY", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
    "",
)

# Role taxonomy (subagent_type without the `forge:` plugin-scope prefix).
_GENERATOR_ROLES = {
    "planner",
    "implementer",
    "health-hygienist",
    "health-fortifier",
    "doc-engineer",
}
_REVIEWER_ROLES = {
    "plan-reviewer",
    "reviewer",
    "health-reviewer",
    "doc-reviewer",
    "final-reviewer",
}
_ASSESSOR_ROLES = {
    "eval-hire",
    "eval-stress",
    "eval-day2",
    "health-auditor",
    "doc-auditor",
}
_ALL_ROLES_BY_LEN = sorted(
    _GENERATOR_ROLES | _REVIEWER_ROLES | _ASSESSOR_ROLES, key=len, reverse=True
)

# Gate-passing ("advance") signals and the ONLY roles allowed to cast them.
# A generator/assessor emitting one is forging a reviewer's ballot (DP3).
_ADVANCE_EMITTERS = {
    "PLAN_APPROVED": {"plan-reviewer"},
    "PHASE_APPROVED": {"reviewer", "health-reviewer", "doc-reviewer"},
    "GO": {"final-reviewer"},
    "VERIFIED": {"reviewer"},  # verification-reviewer is spawned as subagent_type reviewer
}


def _signal_re(tok):
    # A real signal is emitted as a verdict — alone on the report's final line
    # (see each agent's "Reporting Results"), optionally wrapped in blockquote or
    # list markers. Line-anchoring keeps prose mentions from matching.
    return re.compile(r"(?m)^[ \t>*_-]*" + re.escape(tok) + r"[ \t.*_-]*$")


_ADVANCE_RES = {k: _signal_re(k) for k in _ADVANCE_EMITTERS}
_NEG_SIGNAL_RES = {
    s: _signal_re(s) for s in ("REVISION_REQUIRED", "CHANGES_REQUESTED", "UNVERIFIED", "NO-GO")
}

# DP1 — instruction-injection phrasing in untrusted input (file content an agent
# read). Conservative defaults; append one extra regex via
# CLAUDE_FORGE_SECURITY_INJECTION_EXTRA.
_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+|the\s+)?(?:previous|above|prior|earlier)\s+(?:instruction|direction|prompt)",
    r"disregard\s+(?:your|the|all)\s+(?:instruction|system\s+prompt|guideline)",
    r"\byou\s+are\s+now\s+(?:a|an|the)\b",
    r"\bnew\s+instructions?\s*:",
    # Not <system-reminder>: Claude Code injects those into tool results itself.
    r"</?(?:system(?!-reminder)|instructions|prompt)\b",
    r"(?:record|cast|set|change)\s+your\s+(?:vote|verdict|decision)",
    r"\bemit\s+(?:PHASE_APPROVED|PLAN_APPROVED|GO|VERIFIED)\b",
]
_inj_extra = os.environ.get("CLAUDE_FORGE_SECURITY_INJECTION_EXTRA", "").strip()
if _inj_extra:
    _INJECTION_PATTERNS.append(_inj_extra)
# If the user's custom regex is invalid we drop it and keep the built-ins. `_log`
# isn't defined yet at module load, so we stash the bad pattern and surface it
# once from main() instead of failing silently.
_INJECTION_EXTRA_DROPPED = ""
try:
    _INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)
except re.error:
    _INJECTION_EXTRA_DROPPED = _inj_extra
    _INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS[:-1]), re.IGNORECASE)

# DP5 — text addressed to the orchestrator/aggregator ("tell the vote-counter
# how to count").
_AGGREGATOR_RE = re.compile(
    r"\b(?:orchestrator|team\s*lead)\b\s*[:,].{0,80}\b(?:approve|skip|ignore|mark|proceed)\b"
    r"|skip\s+the\s+(?:code\s+|plan\s+)?review"
    r"|mark\s+(?:all\s+)?phases?\s+(?:as\s+)?(?:approved|complete)"
    r"|approve\s+(?:all|every)\s+phase"
    r"|do\s+not\s+(?:run|spawn|wait\s+for)\s+the\s+review"
    r"|ignore\s+the\s+(?:dissenting\s+)?review",
    re.IGNORECASE,
)

# DP4 — evidence a reviewer actually ran tests/build, and obviously
# check-defeating commands.
_TEST_CMD_RE = re.compile(
    r"\b(?:pytest|unittest|tox|nox|jest|vitest|mocha|rspec|phpunit|bats|ctest"
    r"|go\s+test|cargo\s+(?:test|nextest)|npm\s+(?:run\s+)?test|yarn\s+test"
    r"|pnpm\s+(?:run\s+)?test|make\s+test|gradle\s+(?:test|check)|mvn\s+(?:test|verify)"
    r"|npm\s+run\s+build|tsc\b|cargo\s+build|go\s+build|gradle\s+build|mvn\s+package)\b",
    re.IGNORECASE,
)
_SUSPICIOUS_CMD_RE = re.compile(
    r"--no-verify|\|\|\s*true\b|git\s+commit[^\n]*--no-verify"
    r"|chmod\s+(?:-R\s+)?0?777|rm\s+-rf\s+[^\n]*test",
    re.IGNORECASE,
)


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


# ---------------- per-session state ----------------
# Every hook invocation is a fresh process, so correlation state lives on disk.
# It holds prompts, tool inputs and agent reports: the session directory is
# 0700 and every file 0600, the same treatment as the debug log.


def _state_dir(session_id):
    # session_id comes from the hook payload; sanitize before using as a path
    # segment so a malformed value can't escape the tracing tmp root.
    safe = _safe_name(session_id, fallback_prefix="sess")
    d = Path(tempfile.gettempdir()) / "claude-forge-tracing" / safe
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(d, 0o700)
    except Exception:
        pass
    return d


def _write_json(path, obj):
    """Atomic 0600 write: readers in concurrent hook processes never see a torn file."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _append_jsonl(path, obj):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(obj) + "\n")


def _unlink(path):
    try:
        path.unlink()
    except Exception:
        pass


@contextmanager
def _locked(state_dir):
    """Serialize read-modify-write of shared session state. Async hooks and
    parallel agents mean several hook processes can touch it at once."""
    if fcntl is None:
        yield
        return
    fd = os.open(str(state_dir / ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _key_for(tool_name, tool_input):
    """Stable correlation key across Pre/Post when no tool_use_id is provided."""
    src = json.dumps({"t": tool_name, "i": tool_input}, sort_keys=True, default=str)
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:16]


def _agent_id(payload):
    """The subagent a hook fired inside, or "" for the main thread."""
    aid = payload.get("agent_id")
    return _safe_name(aid, fallback_prefix="agent") if aid else ""


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


def _sum_usage(transcript_path, since_ns=0):
    """Sum token usage across assistant lines in a transcript, optionally only
    lines at or after `since_ns`. Returns dict of totals plus a turn count.
    Silently returns zeros if the file is unreadable."""
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
                if since_ns:
                    ts = _parse_ts(d.get("timestamp"))
                    if ts and ts < since_ns:
                        continue
                u = (d.get("message") or {}).get("usage") or {}
                for k in (
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                ):
                    totals[k] += int(u.get(k) or 0)
                totals["turns"] += 1
    except Exception:
        pass
    return totals


def _set_usage_attrs(span, usage):
    """GenAI semconv token attributes, plus Forge's turn count."""
    span.set_attribute("gen_ai.usage.input_tokens", usage["input_tokens"])
    span.set_attribute("gen_ai.usage.output_tokens", usage["output_tokens"])
    span.set_attribute(
        "gen_ai.usage.cache_creation.input_tokens", usage["cache_creation_input_tokens"]
    )
    span.set_attribute("gen_ai.usage.cache_read.input_tokens", usage["cache_read_input_tokens"])
    span.set_attribute("forge.turns", usage["turns"])


def _set_session_usage_attrs(span, usage):
    for k in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "turns",
    ):
        span.set_attribute(f"session.tokens.{k}", usage[k])
    span.set_attribute(
        "session.tokens.total_tokens",
        usage["input_tokens"]
        + usage["output_tokens"]
        + usage["cache_creation_input_tokens"]
        + usage["cache_read_input_tokens"],
    )


def _response_text(resp):
    """Flatten a tool_response (MCP-style content blocks, Bash stdout/stderr,
    or a bare string) into text."""
    if isinstance(resp, str):
        return resp
    if not isinstance(resp, dict):
        return ""
    content = resp.get("content")
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    if isinstance(content, str):
        return content
    if "stdout" in resp or "stderr" in resp:
        return "\n".join(x for x in (resp.get("stdout"), resp.get("stderr")) if x)
    return ""


# ---------------- OpenTelemetry ----------------

_OTEL = None


def _otel():
    """Build the tracer once per process, on first use. Events that only record
    state (every PreToolUse, spawn bookkeeping) never pay for the import."""
    global _OTEL
    if _OTEL is not None:
        return _OTEL
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    # Endpoint, headers and TLS come from the standard OTEL_EXPORTER_OTLP_* env
    # vars; the exporter only goes plaintext for http:// endpoints (or when
    # OTEL_EXPORTER_OTLP_INSECURE=true), so remote collectors get TLS + auth.
    provider = TracerProvider(resource=Resource(attributes={"service.name": "claude-forge"}))
    # Batch so the several spans one event can emit (a result plus security
    # findings) go out in a single export at the process-end flush.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(timeout=2)))
    _OTEL = {
        "trace": trace,
        "provider": provider,
        "tracer": provider.get_tracer("claude-forge.subagents"),
        "propagator": TraceContextTextMapPropagator(),
        "Status": Status,
        "StatusCode": StatusCode,
    }
    return _OTEL


def _safe_flush():
    """Bounded, exception-safe flush so a slow/unreachable OTLP endpoint can't
    hold a hook process open."""
    if _OTEL is None:
        return
    try:
        _OTEL["provider"].force_flush(timeout_millis=FLUSH_TIMEOUT_MS)
    except Exception:
        pass


def _ctx(carrier):
    return _otel()["propagator"].extract(carrier=carrier or {})


def _emit_anchor(name, parent_ctx, attrs, start_ns):
    """Emit a zero-duration span used purely as a parent for child spans."""
    otel = _otel()
    span = otel["tracer"].start_span(name, context=parent_ctx, start_time=start_ns)
    for k, v in attrs.items():
        span.set_attribute(k, v)
    carrier = {}
    otel["propagator"].inject(carrier=carrier, context=otel["trace"].set_span_in_context(span))
    span.end(end_time=start_ns + 1)
    return carrier


def _set_status(span, is_error, msg):
    otel = _otel()
    if is_error:
        span.set_status(otel["Status"](otel["StatusCode"].ERROR, msg))
    else:
        span.set_status(otel["Status"](otel["StatusCode"].OK))


# ---------------- security helpers ----------------


def _role_of(agent_type, name):
    """Resolve a subagent's role from trusted metadata. Prefer `agent_type`
    (forge:<role>, stamped by Claude Code); fall back to keyword-matching the
    label for agents spawned outside the forge taxonomy."""
    cand = (agent_type or "").strip().lower()
    if ":" in cand:
        cand = cand.split(":")[-1]
    if cand in _GENERATOR_ROLES or cand in _REVIEWER_ROLES or cand in _ASSESSOR_ROLES:
        return cand
    text = f"{cand} {(name or '').lower()}"
    for role in _ALL_ROLES_BY_LEN:  # longest-first so plan-reviewer beats reviewer
        if role in text:
            return role
    if "review" in text:
        return "reviewer"
    if "implement" in text:
        return "implementer"
    if "plan" in text:
        return "planner"
    return cand or "unknown"


def _parse_subagent_io(sub_jsonl, since_ns=0, cap=20000):
    """From a subagent's JSONL transcript, return (read_texts, bash_commands):
    read_texts are Read/Grep/Glob results (untrusted file content the agent
    ingested); bash_commands are the commands it ran. Only lines at or after
    `since_ns`, so a resumed agent's earlier segments aren't re-analyzed.
    Bounded + exception-safe."""
    reads, bash = [], []
    if not sub_jsonl:
        return reads, bash
    p = Path(sub_jsonl)
    if not p.exists():
        return reads, bash
    uses = {}
    try:
        with p.open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if since_ns:
                    ts = _parse_ts(d.get("timestamp"))
                    if ts and ts < since_ns:
                        continue
                msg = d.get("message") or {}
                t = d.get("type")
                if t == "assistant":
                    for c in msg.get("content") or []:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            uses[c.get("id") or ""] = {
                                "name": c.get("name") or "",
                                "input": c.get("input") or {},
                            }
                elif t == "user":
                    for c in msg.get("content") or []:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            u = uses.get(c.get("tool_use_id") or "")
                            if not u or u["name"] not in ("Read", "Grep", "Glob"):
                                continue
                            cont = c.get("content")
                            if isinstance(cont, list):
                                cont = "\n".join(
                                    b.get("text", "") for b in cont if isinstance(b, dict)
                                )
                            if isinstance(cont, str) and cont:
                                reads.append(cont[:cap])
        for u in uses.values():
            if u["name"] == "Bash":
                cmd = (u["input"] or {}).get("command") or ""
                if cmd:
                    bash.append(cmd[:cap])
    except Exception:
        pass
    return reads, bash


def _security_state(state_dir):
    data = _read_json(state_dir / "_security.json")
    if not isinstance(data, dict):
        data = {}
    data.setdefault("counts", {})
    data.setdefault("findings", [])
    data.setdefault("timeline", [])
    data.setdefault("roles", [])
    return data


def _write_security_state(state_dir, data):
    try:
        _write_json(state_dir / "_security.json", data)
    except Exception:
        pass


def _record_security_state(state_dir, role, advance, neg):
    data = _security_state(state_dir)
    if len(data["roles"]) < 300:
        data["roles"].append(role)
    if (advance or neg) and len(data["timeline"]) < 300:
        data["timeline"].append({"role": role, "advance": advance, "neg": neg})
    _write_security_state(state_dir, data)


def _emit_security(parent_carrier, state_dir, dp, kind, severity, agent, detail, ts_ns):
    """Emit a security finding as its own short span (status ERROR for
    visibility) and tally it for the session summary. Never raises.
    Callers hold _locked(state_dir)."""
    try:
        span = _otel()["tracer"].start_span(
            f"security:{dp}.{kind}", context=_ctx(parent_carrier), start_time=ts_ns
        )
        span.set_attribute("security.defense_point", dp)
        span.set_attribute("security.kind", kind)
        span.set_attribute("security.severity", severity)
        span.set_attribute("security.agent", agent or "")
        span.set_attribute("security.detail", _truncate(detail, INPUT_LIMIT))
        _set_status(span, True, f"{dp}:{kind}")
        span.end(end_time=ts_ns + 1)
    except Exception:
        pass
    try:
        data = _security_state(state_dir)
        data["counts"][dp] = data["counts"].get(dp, 0) + 1
        if len(data["findings"]) < 100:
            data["findings"].append(
                {
                    "dp": dp,
                    "kind": kind,
                    "severity": severity,
                    "agent": agent,
                    "detail": _truncate(detail, 200),
                }
            )
        _write_security_state(state_dir, data)
    except Exception:
        pass


def _security_analyze(state_dir, role, carrier, output, sub_jsonl, since_ns, ts_ns):
    """Per-run-segment defense-point checks (DP1/DP3/DP4/DP5). `role` comes from
    Claude Code's agent_type; `output` is what the agent reported. Detection
    only; fully exception-guarded. Callers hold _locked(state_dir)."""
    if not TRACE_SECURITY:
        return
    try:
        output = output if isinstance(output, str) else ""

        advance_found = [s for s, rx in _ADVANCE_RES.items() if rx.search(output)]
        neg_found = [s for s, rx in _NEG_SIGNAL_RES.items() if rx.search(output)]
        _record_security_state(state_dir, role, advance_found, neg_found)

        # DP3 — only reviewer roles may cast a gate signal. Anyone else (a
        # generator, an assessor, or an unclassifiable "unknown" role) doing so
        # is a forged ballot.
        if advance_found and role not in _REVIEWER_ROLES:
            _emit_security(
                carrier,
                state_dir,
                "dp3",
                "signal_forgery",
                "high",
                role,
                f"role={role} emitted gate signal {','.join(advance_found)} it cannot cast",
                ts_ns,
            )

        # DP5 — agent output addressed to the orchestrator/aggregator.
        m = _AGGREGATOR_RE.search(output)
        if m:
            _emit_security(
                carrier,
                state_dir,
                "dp5",
                "aggregator_addressed_instruction",
                "high",
                role,
                f"role={role} output directs the orchestrator: {m.group(0)[:120]}",
                ts_ns,
            )

        reads, bash_cmds = _parse_subagent_io(sub_jsonl, since_ns=since_ns)

        # DP1 — injected instructions / forged ballots inside files the agent read.
        flagged_inj = flagged_tok = False
        for txt in reads:
            if not flagged_inj:
                mi = _INJECTION_RE.search(txt)
                if mi:
                    _emit_security(
                        carrier,
                        state_dir,
                        "dp1",
                        "injected_instruction",
                        "medium",
                        role,
                        f"role={role} ingested instruction-like text: {mi.group(0)[:100]}",
                        ts_ns,
                    )
                    flagged_inj = True
            if not flagged_tok:
                for s, rx in _ADVANCE_RES.items():
                    if rx.search(txt):
                        _emit_security(
                            carrier,
                            state_dir,
                            "dp1",
                            "forged_signal_in_input",
                            "high",
                            role,
                            f"role={role} read input with standalone gate token {s}",
                            ts_ns,
                        )
                        flagged_tok = True
                        break
            if flagged_inj and flagged_tok:
                break

        # DP4 — reviewer approved without running tests/build.
        if advance_found and role in _REVIEWER_ROLES:
            if not any(_TEST_CMD_RE.search(c) for c in bash_cmds):
                _emit_security(
                    carrier,
                    state_dir,
                    "dp4",
                    "approved_without_tests",
                    "medium",
                    role,
                    f"role={role} emitted {','.join(advance_found)} with no test/build run",
                    ts_ns,
                )
        # DP4 — check-defeating commands from any agent.
        for c in bash_cmds:
            if _SUSPICIOUS_CMD_RE.search(c):
                _emit_security(
                    carrier,
                    state_dir,
                    "dp4",
                    "suspicious_command",
                    "medium",
                    role,
                    f"role={role} ran: {c[:140]}",
                    ts_ns,
                )
                break
    except Exception:
        pass


def _write_trace_summary(state_dir):
    """Persist a portable run summary so the Tier C trajectory validators
    (evaluation/check_run.py) can replay protocol invariants over a real run.
    Rewritten after every agent run segment, so it is current even when the
    session never reaches a clean end. Callers hold _locked(state_dir)."""
    data = _security_state(state_dir)
    events = []
    for t in data.get("timeline") or []:
        role = t.get("role")
        for sig in (t.get("advance") or []) + (t.get("neg") or []):
            events.append({"role": role, "signal": sig})
    try:
        _write_json(
            state_dir / "trace-summary.json",
            {
                "roles": data.get("roles") or [],
                "events": events,
                "security": {
                    "counts": data.get("counts") or {},
                    "findings": data.get("findings", []),
                },
            },
        )
    except Exception:
        pass


def _security_session_summary(state_dir, root_carrier, span, end_ns):
    """Session-level DP2 fan-out precondition + DP5 decision-starvation, then
    write the per-session security summary onto the session_complete span.
    Callers hold _locked(state_dir)."""
    if not TRACE_SECURITY:
        return
    try:
        data = _security_state(state_dir)
        roles = data.get("roles") or []
        timeline = data.get("timeline") or []
        # A resumed session emits session_complete again; each session-level
        # finding is raised once, or its counts and trace-summary.json double.
        raised = set(data.get("session_findings") or [])

        def once(key):
            if key in raised:
                return False
            raised.add(key)
            state = _security_state(state_dir)
            state["session_findings"] = sorted(raised)
            _write_security_state(state_dir, state)
            return True

        # DP2 — correlated-compromise precondition: multiple read-only assessors
        # fanned out over the same input on the shared session model.
        assessor_runs = sum(1 for r in roles if r in _ASSESSOR_ROLES)
        if assessor_runs >= 2 and once("dp2:shared_model_fanout"):
            _emit_security(
                root_carrier,
                state_dir,
                "dp2",
                "shared_model_fanout",
                "info",
                "intake",
                f"{assessor_runs} read-only assessors fanned out over the same input on the "
                "shared session model (one injection could transfer to all)",
                end_ns,
            )

        # DP5 — a reviewer that only ever requested changes (denial of decision).
        agg = {}
        for t in timeline:
            a = agg.setdefault(t.get("role") or "?", {"adv": 0, "neg": 0})
            a["adv"] += len(t.get("advance") or [])
            a["neg"] += len(t.get("neg") or [])
        for r, a in agg.items():
            if r in _REVIEWER_ROLES and a["neg"] >= 3 and a["adv"] == 0 and once(f"dp5:decision_starvation:{r}"):
                _emit_security(
                    root_carrier,
                    state_dir,
                    "dp5",
                    "decision_starvation",
                    "low",
                    r,
                    f"{r} returned {a['neg']} change requests with no approval",
                    end_ns,
                )

        # Summary (re-read so the DP2/DP5 emits above are counted).
        data = _security_state(state_dir)
        counts = data.get("counts") or {}
        total = sum(counts.values())
        span.set_attribute("forge.security.findings_total", total)
        span.set_attribute("forge.security.flagged", total > 0)
        for dp in ("dp1", "dp2", "dp3", "dp4", "dp5"):
            span.set_attribute(f"forge.security.{dp}", counts.get(dp, 0))
        if timeline:
            tl = ">".join(
                f"{t.get('role')}:{'/'.join((t.get('advance') or []) + (t.get('neg') or []))}"
                for t in timeline
            )
            span.set_attribute("forge.security.signal_timeline", _truncate(tl, OUTPUT_LIMIT))
        _write_trace_summary(state_dir)
    except Exception:
        pass


# ---------------- session handlers ----------------


def _ensure_root(state_dir, session_id, prompt="", cwd="", transcript_path=""):
    """Create the session's root anchor once. Reuses an existing _root.json so
    the trace_id stays stable across the whole pipeline. Called from
    UserPromptSubmit (not SessionStart, which carries no prompt to name the
    trace after) and, defensively, before any agent anchor."""
    root_file = state_dir / "_root.json"
    if root_file.exists():
        return  # already anchored — keep the same trace_id
    with _locked(state_dir):
        if root_file.exists():
            return
        start_ns = time.time_ns()
        name = f"session: {prompt.splitlines()[0][:80]}" if prompt else f"session: {session_id[:8]}"
        carrier = _emit_anchor(
            name,
            parent_ctx=None,
            attrs={
                "session.id": session_id,
                "gen_ai.conversation.id": session_id,
                "session.cwd": cwd,
                "user.prompt": _truncate(prompt, PROMPT_LIMIT),
            },
            start_ns=start_ns,
        )
        _write_json(
            root_file,
            {
                "carrier": carrier,
                "start_ns": start_ns,
                "prompt": _truncate(prompt, PROMPT_LIMIT),
                "cwd": cwd,
                "transcript_path": transcript_path,
            },
        )
    _log(f"root anchor created for session={session_id[:8]} name={name!r}")


def _ensure_root_from(payload, state_dir):
    _ensure_root(
        state_dir,
        session_id=payload.get("session_id") or "default",
        prompt=payload.get("prompt") or "",
        cwd=payload.get("cwd") or "",
        transcript_path=payload.get("transcript_path") or "",
    )


def _root_carrier(state_dir):
    root = _read_json(state_dir / "_root.json")
    return (root or {}).get("carrier") or {}


def _emit_session_complete(payload, state_dir):
    """SessionEnd: emit the session_complete span, covering every turn of the
    session. Stop is not used: it fires after every turn, so a summary emitted
    there covered only the first. Idempotent per session end; a resumed
    session (UserPromptSubmit after completion) re-arms it."""
    with _locked(state_dir):
        root = _read_json(state_dir / "_root.json")
        if not root or root.get("complete_emitted"):
            return
        is_error = bool(root.get("error"))
        end_ns = time.time_ns()
        start_ns = root.get("start_ns") or end_ns
        span = _otel()["tracer"].start_span(
            "session_complete", context=_ctx(root.get("carrier")), start_time=start_ns
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
            base = transcript.removesuffix(".jsonl")
            sub_dir = Path(base) / "subagents"
            if sub_dir.is_dir():
                for jsonl in sub_dir.glob("agent-*.jsonl"):
                    u = _sum_usage(str(jsonl))
                    for k in totals:
                        totals[k] += u[k]
            _set_session_usage_attrs(span, totals)
        _set_status(span, is_error, "session ended in failure")
        # Session-level defense-in-depth summary (DP2 fan-out, DP5 starvation, counts).
        _security_session_summary(state_dir, root.get("carrier") or {}, span, end_ns)
        span.end(end_time=end_ns)
        # Mark complete; keep _root.json so subagent calls after a Stop can still
        # parent under the same trace_id. State accumulates in /tmp but the OS reaps it.
        try:
            root["complete_emitted"] = True
            root["stopped_at_ns"] = end_ns
            _write_json(state_dir / "_root.json", root)
        except Exception:
            pass


def _handle_stop_failure(payload, state_dir):
    """A turn ended on an API error: the session_complete emitted at
    SessionEnd carries ERROR status."""
    with _locked(state_dir):
        root = _read_json(state_dir / "_root.json")
        if root:
            root["error"] = True
            _write_json(state_dir / "_root.json", root)


def _handle_user_prompt(payload, state_dir):
    """Root anchor on the first prompt. A prompt after the session completed
    (claude --resume) re-arms completion for the next SessionEnd."""
    _ensure_root_from(payload, state_dir)
    with _locked(state_dir):
        root = _read_json(state_dir / "_root.json")
        if root and root.get("complete_emitted"):
            root["complete_emitted"] = False
            _write_json(state_dir / "_root.json", root)


def _parent_ctx(state_dir, agent_id=""):
    """The agent's anchor when the event fired inside a subagent, else the
    session root, else None."""
    if agent_id:
        state = _read_json(state_dir / f"agent_{agent_id}.json")
        if state and state.get("carrier"):
            return _ctx(state["carrier"])
    carrier = _root_carrier(state_dir)
    return _ctx(carrier) if carrier else None


def _handle_permission(payload, state_dir, denied):
    """Emit a span for permission events so Jaeger shows when a tool was asked
    to be approved or was blocked, under the agent that asked."""
    parent_ctx = _parent_ctx(state_dir, _agent_id(payload))
    if parent_ctx is None:
        return
    now_ns = time.time_ns()
    tool = payload.get("tool_name") or "?"
    op = "permission_denied" if denied else "permission_requested"
    span = _otel()["tracer"].start_span(f"{op}:{tool}", context=parent_ctx, start_time=now_ns)
    span.set_attribute("permission.tool", tool)
    span.set_attribute("permission.denied", denied)
    reason = payload.get("reason") or payload.get("message") or ""
    if reason:
        span.set_attribute("permission.reason", _truncate(reason, INPUT_LIMIT))
    if denied:
        _set_status(span, True, "permission denied")
    span.end(end_time=now_ns + 1)


def _handle_pre_compact(payload, state_dir):
    """Stash compaction start time; PostCompact emits the span."""
    try:
        _write_json(state_dir / "_compaction.json", {"start_ns": time.time_ns()})
    except Exception:
        pass


def _handle_post_compact(payload, state_dir):
    f = state_dir / "_compaction.json"
    end_ns = time.time_ns()
    start_ns = ((_read_json(f) or {}).get("start_ns")) or end_ns
    parent_ctx = _parent_ctx(state_dir, _agent_id(payload))
    if parent_ctx is None:
        return
    span = _otel()["tracer"].start_span("compaction", context=parent_ctx, start_time=start_ns)
    span.set_attribute("compaction.duration_ms", (end_ns - start_ns) // 1_000_000)
    span.end(end_time=end_ns)
    _unlink(f)


def _handle_instructions_loaded(payload, state_dir):
    """Annotate the trace when CLAUDE.md or rule files load — useful when an
    instruction file shapes agent behavior unexpectedly."""
    parent_ctx = _parent_ctx(state_dir, _agent_id(payload))
    if parent_ctx is None:
        return
    now_ns = time.time_ns()
    path = payload.get("file_path") or payload.get("path") or "?"
    inst_type = payload.get("memory_type") or payload.get("type") or ""
    span = _otel()["tracer"].start_span(
        "instructions.loaded", context=parent_ctx, start_time=now_ns
    )
    span.set_attribute("instructions.path", str(path))
    if inst_type:
        span.set_attribute("instructions.type", str(inst_type))
    span.end(end_time=now_ns + 1)


# ---------------- agent handlers ----------------
# Lifecycle, as observed from Claude Code with Agent Teams on:
#   PreToolUse Agent → PostToolUse Agent {status: async_launched, agentId}
#   → SubagentStart {agent_id} → tool events carrying agent_id
#   → SubagentStop {agent_id, agent_transcript_path, last_assistant_message}
# and on each orchestrator SendMessage to that agent:
#   PostToolUse SendMessage {resumedAgentId} → SubagentStart (same agent_id)
#   → ... → SubagentStop.
# Each SubagentStart opens a run segment; each SubagentStop closes the oldest
# open one, so segment accounting holds even when async Stop hooks run late.


def _record_spawn(payload, state_dir, tool_use_id):
    """Main-thread PreToolUse Agent: remember what the orchestrator asked for,
    so the agent's anchor can carry its name and prompt."""
    ti = payload.get("tool_input") or {}
    _write_json(
        state_dir / f"spawn_{tool_use_id}.json",
        {
            "name": ti.get("name") or "",
            "description": ti.get("description") or "",
            "subagent_type": ti.get("subagent_type") or "general-purpose",
            "prompt": _truncate(ti.get("prompt") or "", PROMPT_LIMIT),
            "ts": time.time_ns(),
        },
    )


def _bind_spawn(payload, state_dir, tool_use_id):
    """Main-thread PostToolUse Agent: the launch receipt carries the agentId.
    Bind the spawn metadata to it, and the agent's name to its id."""
    resp = payload.get("tool_response")
    aid = resp.get("agentId") if isinstance(resp, dict) else None
    if not aid:
        return
    aid = _safe_name(aid, fallback_prefix="agent")
    name = (payload.get("tool_input") or {}).get("name") or ""
    with _locked(state_dir):
        if name:
            names = _read_json(state_dir / "_agent_names.json") or {}
            names[name] = aid
            _write_json(state_dir / "_agent_names.json", names)
        src = state_dir / f"spawn_{tool_use_id}.json"
        if src.exists():  # SubagentStart may already have claimed it by type
            os.replace(src, state_dir / f"spawnmeta_{aid}.json")


def _take_spawn_meta(state_dir, aid, agent_type):
    """Spawn metadata for a starting agent: the exact agentId binding if the
    launch receipt was seen, else the oldest unclaimed spawn of that type.
    Callers hold _locked(state_dir)."""
    exact = state_dir / f"spawnmeta_{aid}.json"
    meta = _read_json(exact)
    if meta:
        _unlink(exact)
        return meta
    pending = []
    for f in state_dir.glob("spawn_*.json"):
        m = _read_json(f)
        if m and m.get("subagent_type") == (agent_type or "general-purpose"):
            pending.append((m.get("ts") or 0, str(f), m))
    if not pending:
        return {}
    pending.sort()
    _unlink(Path(pending[0][1]))
    return pending[0][2]


def _open_agent(payload, state_dir, aid, now_ns):
    """Create the agent's anchor span and state. Callers hold _locked(state_dir)
    and have checked no state exists, so one agent_id never gets two anchors."""
    agent_type = payload.get("agent_type") or ""
    meta = _take_spawn_meta(state_dir, aid, agent_type)
    label = meta.get("description") or meta.get("name") or agent_type or "subagent"
    role = _role_of(agent_type, meta.get("name") or label)
    session_id = payload.get("session_id") or "default"
    root = _root_carrier(state_dir)
    carrier = _emit_anchor(
        f"subagent:{label}",
        parent_ctx=_ctx(root) if root else None,
        attrs={
            "gen_ai.agent.id": aid,
            "gen_ai.agent.name": meta.get("name") or agent_type or label,
            "gen_ai.agent.description": meta.get("description") or "",
            "gen_ai.conversation.id": session_id,
            "agent.subagent_type": agent_type,
            "agent.prompt": meta.get("prompt") or "",
            "forge.role": role,
        },
        start_ns=now_ns,
    )
    state = {
        "carrier": carrier,
        "label": label,
        "name": meta.get("name") or "",
        "role": role,
        "agent_type": agent_type,
        "segments": [now_ns],
        "closed": 0,
    }
    _write_json(state_dir / f"agent_{aid}.json", state)
    return state


def _handle_subagent_start(payload, state_dir):
    aid = _agent_id(payload)
    if not aid:
        return
    now_ns = time.time_ns()
    _ensure_root_from(payload, state_dir)
    with _locked(state_dir):
        sf = state_dir / f"agent_{aid}.json"
        state = _read_json(sf)
        if state:  # resumed by SendMessage: same anchor, new run segment
            state["segments"].append(now_ns)
            _write_json(sf, state)
            return
        _open_agent(payload, state_dir, aid, now_ns)


def _agent_state(payload, state_dir, aid):
    """State for an agent, creating its anchor if SubagentStart was missed
    (e.g. tracing enabled mid-run)."""
    sf = state_dir / f"agent_{aid}.json"
    state = _read_json(sf)
    if state:
        return state
    _ensure_root_from(payload, state_dir)
    with _locked(state_dir):
        state = _read_json(sf)
        if state:
            return state
        return _open_agent(payload, state_dir, aid, time.time_ns())


def _reports_since(state_dir, aid, since_ns):
    reports = []
    try:
        with open(state_dir / f"reports_{aid}.jsonl") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("ts") or 0) >= since_ns and isinstance(r.get("message"), str):
                    reports.append(r["message"])
    except Exception:
        pass
    return reports


def _handle_subagent_stop(payload, state_dir):
    aid = _agent_id(payload)
    if not aid:
        return
    end_ns = time.time_ns()
    _agent_state(payload, state_dir, aid)
    with _locked(state_dir):
        sf = state_dir / f"agent_{aid}.json"
        state = _read_json(sf) or {}
        segments = state.get("segments") or [end_ns]
        idx = min(state.get("closed", 0), len(segments) - 1)
        seg_start = segments[idx]
        state["closed"] = idx + 1
        _write_json(sf, state)

    label = state.get("label") or "subagent"
    role = state.get("role") or "unknown"
    carrier = state.get("carrier") or {}
    # The agent's report is its SendMessage(to="main"); plain text never reaches
    # the orchestrator. Fall back to the last message only for the span output.
    reports = _reports_since(state_dir, aid, seg_start)
    output = "\n\n".join(reports) or (payload.get("last_assistant_message") or "")

    span = _otel()["tracer"].start_span(
        f"subagent_result:{label}", context=_ctx(carrier), start_time=seg_start
    )
    span.set_attribute("gen_ai.operation.name", "invoke_agent")
    span.set_attribute("gen_ai.agent.id", aid)
    span.set_attribute("gen_ai.agent.name", state.get("name") or state.get("agent_type") or label)
    span.set_attribute("gen_ai.conversation.id", payload.get("session_id") or "default")
    span.set_attribute("forge.role", role)
    span.set_attribute("agent.segment", idx + 1)
    span.set_attribute("agent.duration_ms", (end_ns - seg_start) // 1_000_000)
    span.set_attribute("agent.output", _truncate(output, OUTPUT_LIMIT))
    # An agent that stops without SendMessage(to="main") leaves the orchestrator
    # with a bare idle notification — the failure mode the protocol warns about.
    span.set_attribute("agent.reported", bool(reports))
    transcript = payload.get("agent_transcript_path") or ""
    if transcript:
        span.set_attribute("agent.transcript_path", transcript)
        _set_usage_attrs(span, _sum_usage(transcript, since_ns=seg_start))
    _set_status(span, False, "")
    span.end(end_time=end_ns)

    # Passive defense-in-depth pass: provenance, injected input, approve-without-
    # tests, aggregator-directed instructions. Detection only; never blocks.
    with _locked(state_dir):
        _security_analyze(
            state_dir,
            role,
            carrier,
            "\n\n".join(reports) or output,
            transcript,
            seg_start,
            end_ns,
        )
        _write_trace_summary(state_dir)


def _handle_orchestrator_message(payload, state_dir):
    """Main-thread SendMessage: the orchestrator continuing an agent. Parent the
    span to that agent (the receipt names the resumed agentId)."""
    ti = payload.get("tool_input") or {}
    to = (ti.get("to") or "").strip()
    resp = payload.get("tool_response")
    aid = resp.get("resumedAgentId") if isinstance(resp, dict) else None
    if not aid and to:
        aid = (_read_json(state_dir / "_agent_names.json") or {}).get(to)
    aid = _safe_name(aid, fallback_prefix="agent") if aid else ""
    state = _read_json(state_dir / f"agent_{aid}.json") if aid else None
    carrier = (state or {}).get("carrier") or _root_carrier(state_dir)
    if not carrier:
        return
    now_ns = time.time_ns()
    label = (state or {}).get("label") or to or "agent"
    span = _otel()["tracer"].start_span(
        f"message:{label}", context=_ctx(carrier), start_time=now_ns
    )
    span.set_attribute("sendmessage.to", to)
    span.set_attribute("sendmessage.summary", ti.get("summary") or "")
    msg = ti.get("message")
    span.set_attribute(
        "agent.prompt",
        _truncate(msg if isinstance(msg, str) else json.dumps(msg, default=str), PROMPT_LIMIT),
    )
    if aid:
        span.set_attribute("gen_ai.agent.id", aid)
    span.end(end_time=now_ns + 1)


# ---------------- tool handlers ----------------


def _should_trace_tool(tool_name):
    """Mutational tools trace by default; everything else needs TRACE_INNER=1
    and isn't in the blocklist."""
    if tool_name in MUTATION_TOOLS:
        return TRACE_MUTATIONS
    return TRACE_INNER and tool_name not in INNER_TOOL_BLOCKLIST


def _handle_tool_pre(payload, state_dir, tool_use_id):
    """State only — never imports OpenTelemetry, so the synchronous PreToolUse
    hook stays cheap."""
    tool_name = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    aid = _agent_id(payload)
    if not aid and tool_name == "Agent":
        _record_spawn(payload, state_dir, tool_use_id)
        return
    if aid and tool_name == "SendMessage" and (ti.get("to") or "").strip() == "main":
        msg = ti.get("message")
        _append_jsonl(
            state_dir / f"reports_{aid}.jsonl",
            {
                "ts": time.time_ns(),
                "message": msg if isinstance(msg, str) else json.dumps(msg, default=str),
            },
        )
    if aid and tool_name == "StructuredOutput":
        # /forge:run agents report through a typed StructuredOutput call. Put
        # the signal on its own final line, as a SendMessage report carries it,
        # so the line-anchored signal detectors read both channels alike.
        body = {k: v for k, v in ti.items() if k != "signal"}
        _append_jsonl(
            state_dir / f"reports_{aid}.jsonl",
            {
                "ts": time.time_ns(),
                "message": f"{json.dumps(body, default=str)}\n{ti.get('signal') or ''}",
            },
        )
    if not aid and tool_name == "SendMessage":
        return  # handled at PostToolUse, where the receipt names the agent
    if not _should_trace_tool(tool_name):
        return
    _write_json(
        state_dir / f"tool_{tool_use_id}.json",
        {
            "start_ns": time.time_ns(),
            "tool_name": tool_name,
            "tool_input": ti,
            "agent_id": aid,
        },
    )


def _handle_tool_post(payload, state_dir, tool_use_id, is_failure):
    tool_name = payload.get("tool_name") or ""
    aid = _agent_id(payload)
    if not aid and tool_name == "Agent":
        _bind_spawn(payload, state_dir, tool_use_id)
        return
    if not aid and tool_name == "SendMessage":
        _handle_orchestrator_message(payload, state_dir)
        return
    sf = state_dir / f"tool_{tool_use_id}.json"
    saved = _read_json(sf)
    if not saved:
        return
    _unlink(sf)
    end_ns = time.time_ns()
    start_ns = saved.get("start_ns") or end_ns
    if aid:
        _agent_state(payload, state_dir, aid)
    parent_ctx = _parent_ctx(state_dir, aid)
    span = _otel()["tracer"].start_span(
        f"tool:{tool_name}", context=parent_ctx, start_time=start_ns
    )
    span.set_attribute("gen_ai.operation.name", "execute_tool")
    span.set_attribute("gen_ai.tool.name", tool_name)
    span.set_attribute("gen_ai.tool.call.id", payload.get("tool_use_id") or "")
    if aid:
        span.set_attribute("gen_ai.agent.id", aid)
    span.set_attribute("tool.duration_ms", (end_ns - start_ns) // 1_000_000)
    try:
        span.set_attribute(
            "tool.input",
            _truncate(json.dumps(saved.get("tool_input") or {}, default=str), INPUT_LIMIT),
        )
    except Exception:
        pass
    resp = payload.get("tool_response")
    is_error = is_failure or (
        isinstance(resp, dict) and bool(resp.get("isError") or resp.get("is_error"))
    )
    span.set_attribute("tool.is_error", is_error)
    text = _response_text(resp) or payload.get("error") or ""
    if text:
        span.set_attribute("tool.output", _truncate(text, OUTPUT_LIMIT))
    if is_error:
        _set_status(span, True, "tool reported error")
    span.end(end_time=end_ns)


# ---------------- entry point ----------------

_HANDLERS = {
    "UserPromptSubmit": _handle_user_prompt,
    "SessionEnd": _emit_session_complete,
    "StopFailure": _handle_stop_failure,
    "PermissionDenied": lambda p, d: _handle_permission(p, d, denied=True),
    "PermissionRequest": lambda p, d: _handle_permission(p, d, denied=False),
    "PreCompact": _handle_pre_compact,
    "PostCompact": _handle_post_compact,
    "InstructionsLoaded": _handle_instructions_loaded,
    "SubagentStart": _handle_subagent_start,
    "SubagentStop": _handle_subagent_stop,
}


def main():
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    _log(
        f"called tracing={os.environ.get('CLAUDE_FORGE_TRACING', 'UNSET')} bytes={len(raw)}",
        raw,
    )
    if _INJECTION_EXTRA_DROPPED:
        _log(
            "dropped invalid CLAUDE_FORGE_SECURITY_INJECTION_EXTRA regex "
            f"(kept built-in patterns): {_INJECTION_EXTRA_DROPPED!r}"
        )

    if not _env_truthy("CLAUDE_FORGE_TRACING"):
        _exit_ok()

    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        _exit_ok()

    event = payload.get("hook_event_name") or ""
    session_id = payload.get("session_id") or "default"

    try:
        state_dir = _state_dir(session_id)
    except Exception:
        _exit_ok()

    try:
        if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            tool_input = payload.get("tool_input") or {}
            raw_tool_use_id = payload.get("tool_use_id") or _key_for(
                payload.get("tool_name") or "", tool_input
            )
            # tool_use_id becomes a filename (spawn_<id>.json / tool_<id>.json),
            # so strip anything that could escape the state dir.
            tool_use_id = _safe_name(raw_tool_use_id, fallback_prefix="tu")
            if event == "PreToolUse":
                _handle_tool_pre(payload, state_dir, tool_use_id)
            else:
                _handle_tool_post(
                    payload,
                    state_dir,
                    tool_use_id,
                    is_failure=event == "PostToolUseFailure",
                )
        elif event in _HANDLERS:
            _HANDLERS[event](payload, state_dir)
    except Exception as e:
        _log(f"handler error event={event}: {e!r}")

    _safe_flush()
    _exit_ok()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
