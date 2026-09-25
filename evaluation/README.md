# Evaluation

How we know the Forge team still behaves as its prompts, tools, and the
orchestrator keep changing. Structured as an evaluation pyramid — fast and
deterministic at the base, realistic at the top.

| Tier | What | Where | Cadence |
|------|------|-------|---------|
| **A — Contracts** | Deterministic structural checks (frontmatter, tool policy, wiring, manifests) | `tier_a_contracts/` | Every push / PR |
| **B — Single agent** | `claude plugin eval` cases: one agent against a scaffolded fixture, scored against a no-plugin baseline | `../evals/` | On demand (LLM-costed) |
| **C — Trajectory** | Governance-signal order validators (provenance, gate order, no skipped review) | `tier_c_trajectory/` + `check_run.py` | Synthetic per-PR; real runs nightly |
| **D — Live traces** | OpenTelemetry → Jaeger, plus `security:dp{1..5}.*` spans; a replay of recorded hook events guards the hook itself | `../hooks/trace_subagents.py`, `tier_d_tracing/` | Replay per-PR; traces on real runs |

Forge is itself an evaluation system (its adversarial reviewers are in-pipeline
rubric judges). This harness evaluates *Forge* — so refactors like the
native-subagent migration can't silently break the team.

## Run it

```bash
python -m pip install pytest
python -m pytest evaluation/ -v          # Tier A + Tier C
python -m pytest evaluation/tier_a_contracts
python -m pytest evaluation/tier_c_trajectory
```

No network, no LLM, no API keys — Tiers A and C are pure-stdlib + pytest. The Tier D replay
also needs `opentelemetry-sdk` (spans go to an in-memory exporter) and skips without it:

```bash
python -m pip install pytest opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
python -m pytest evaluation/tier_d_tracing
```

## Tier A — contracts

Registry-driven (`lib/registry.py` loads `agents/`), so new roles are picked up
automatically. Freezes the invariants from the native-subagent migration:

- every agent has `name` / `description` / `tools`; `name` is lowercase-hyphen and matches the filename; names are unique
- the team is exactly the 15 expected roles — no missing, no stray files
- **tool policy per role class**: generators get `Write`+`Edit`; reviewers get `Edit` but not `Write` (read-only over source); assessors are fully read-only; **no role gets `Agent`** (no nesting); tools stay within the allowed vocabulary
- every `forge:<type>` referenced under `skills/` resolves to an agent file, and every agent is referenced (no dangling refs, no orphans)
- `pipeline-protocol.md` lists every role; `plugin.json` declares no `agents` field (auto-discovery preserved); `plugin.json` / `marketplace.json` versions agree; the `CHANGELOG` has the current version
- the trace hook's role taxonomy and signal-authorization map match the registry (security/eval can't drift from the agents)

## Tier C — trajectory

A final report can read "GO — all phases approved" even when a gate was skipped
or a signal was forged. `lib/trajectory.py` validates the sequence of governance
signals against protocol invariants:

- **signal provenance** — only authorized roles cast `PLAN_APPROVED` / `PHASE_APPROVED` / `GO` / `VERIFIED` (a generator casting one is a forged ballot)
- **gate order** — no `PHASE_APPROVED` before the plan is approved; no `GO`/`VERIFIED` before a phase is approved
- **no skipped review** — every `PHASE_APPROVED` is backed by an `IMPLEMENTATION_COMPLETE`

The per-PR tests run these against synthetic legal/illegal trajectories. For real
runs, the trace hook persists `trace-summary.json` into its per-session state
dir; replay the validators over it:

```bash
# after a pipeline run with CLAUDE_FORGE_TRACING=1
python evaluation/check_run.py --latest
python evaluation/check_run.py /tmp/claude-forge-tracing/<session>/trace-summary.json
```

This is the bridge from Tier D (observability) to Tier C (assertion): the same
trace data that powers Jaeger also proves the run followed the protocol.

## Tier D — hook replay

`tier_d_tracing/test_hook_replay.py` feeds the trace hook a hook-event sequence
shaped like a live Agent Teams run: two evaluators spawned in parallel (the
Agent tool's PostToolUse is only a `status: async_launched` receipt), tool
calls carrying `agent_id`, a `SendMessage` resume, and `SubagentStop` per run
segment. It asserts on the resulting spans: each agent's tool spans parent to
its own anchor, results come from `SubagentStop` with the agent's
`SendMessage(to="main")` report, a resume is a second segment on the same
anchor, a forged gate signal raises `security:dp3.signal_forgery`, and
`session_complete` waits until no background work is in flight.

## Tier B — plugin evals

`../evals/` holds [`claude plugin eval`](https://code.claude.com/docs/en/plugin-evals)
cases. Each scaffolds a small repo, asks for one Forge agent, and grades the
outcome; every case also runs without the plugin, so the report shows what the
agent adds over plain Claude.

| Case | Fixture | Graded on |
|------|---------|-----------|
| `reviewer-catches-spec-violation` | Phase 1 committed; its tests pass, but truncation can leave a trailing hyphen, which the spec forbids and no test covers | the violation is named in `feedback.md` (LLM judge); `CHANGES_REQUESTED`; no edits or new files under `src/` or `tests/` |

```bash
# from the repo root; --scaffold runs the case's own fixture script
claude plugin eval . --scaffold --allow-tools Bash Edit
```

Granting `Bash` requires Claude Code's OS sandbox (on Linux: `bubblewrap` and
`socat`). Without it, grant only `Edit`: the reviewer then reviews by reading,
without running the tests.
