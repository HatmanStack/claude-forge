# Evaluation

How we know the Forge team still behaves as its prompts, tools, and the
orchestrator keep changing. Structured as an evaluation pyramid — fast and
deterministic at the base, realistic at the top.

| Tier | What | Where | Cadence |
|------|------|-------|---------|
| **A — Contracts** | Deterministic structural checks (frontmatter, tool and model policy, wiring, manifests) | `tier_a_contracts/` | Every push / PR |
| **B — Single agent** | `claude plugin eval` cases: one agent against a scaffolded fixture, scored against a no-plugin baseline | `../evals/` | On demand (LLM-costed) |
| **C — Trajectory** | Governance-signal order validators (provenance, gate order, no skipped review), and `/forge:run` replayed against scripted agent replies | `tier_c_trajectory/` + `check_run.py` | Per-PR; real runs via `check_run.py` |
| **D — Live traces** | OpenTelemetry → Jaeger, plus `security:dp{1..5}.*` spans; a replay of recorded hook events guards the hook itself | `../hooks/trace_subagents.py`, `tier_d_tracing/` | Replay per-PR; traces on real runs |

Forge is itself an evaluation system (its adversarial reviewers are in-pipeline
rubric judges). This harness evaluates *Forge* — so refactors like the
native-subagent migration can't silently break the team.

## Run it

```bash
python -m pip install pytest
python -m pytest evaluation/ -v          # Tier A + Tier C (+ Tier D if OpenTelemetry is installed)
python -m pytest evaluation/tier_a_contracts
python -m pytest evaluation/tier_c_trajectory
node --test evaluation/tier_c_trajectory/workflow_run.test.mjs   # /forge:run orchestration
```

No network, no LLM, no API keys — Tiers A and C are pure-stdlib + pytest, plus Node for the workflow trajectories. The Tier D replay
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
- **model policy**: every agent pins `model` (opus for the Planner and every gate, sonnet for generators and assessors), and `workflows/run.js` pins the same model per role
- every skill is user-invoked (`disable-model-invocation: true`)
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

### `/forge:run` orchestration

`workflow_run.test.mjs` runs `workflows/run.js` with a stub `agent()` that
returns scripted reports, and asserts on the sequence of roles it spawns:

- gate order per flow, and each loop stopping at 3 iterations
- resume entry points: approved phases skipped, open feedback resumes at the
  implementer, an unreviewed phase at the reviewer, existing plan files at plan review
- phase tags routing to their implementer/reviewer pair, and each flow's default pair
- the unified audit re-planning significant unverified findings exactly once
- a recorded verdict stopping the run unless `rework` is passed
- every agent pinning a non-Fable model, and no agent asked to write under `.claude/`

## Tier D — hook replay

`tier_d_tracing/test_hook_replay.py` feeds the trace hook a hook-event sequence
shaped like a live Agent Teams run: two evaluators spawned in parallel (the
Agent tool's PostToolUse is only a `status: async_launched` receipt), tool
calls carrying `agent_id`, a `SendMessage` resume, and `SubagentStop` per run
segment. It asserts on the resulting spans: each agent's tool spans parent to
its own anchor, results come from `SubagentStop` with the agent's
`SendMessage(to="main")` report, a resume is a second segment on the same
anchor, a forged gate signal raises `security:dp3.signal_forgery`, and
`session_complete` is emitted once, at `SessionEnd`: never on `Stop`, marked ERROR after a `StopFailure`, and again after a resume.

## Tier B — plugin evals

`../evals/` holds [`claude plugin eval`](https://code.claude.com/docs/en/plugin-evals)
cases. Each scaffolds a small repo, asks for one Forge agent, and grades the
outcome; every case also runs without the plugin, so the report shows what the
agent adds over plain Claude.

| Case | Fixture | Graded on |
|------|---------|-----------|
| `reviewer-catches-spec-violation` | Phase 1 committed; its tests pass, but truncation can leave a trailing hyphen, which the spec forbids and no test covers | the violation is named in `feedback.md` (LLM judge); `CHANGES_REQUESTED`; no edits or new files under `src/` or `tests/` |
| `reviewer-catches-standards-violation` | Meets the spec, tests pass, but prints from library code and swallows errors against Phase-0 | both violations named in `feedback.md` (LLM judge); `CHANGES_REQUESTED`; source untouched |
| `reviewer-approves-clean-phase` | Correct, fully tested, conventional (tag `needs-bash`) | `PHASE_APPROVED`, no `OPEN` items, no change request; source untouched |

```bash
# from the repo root; --scaffold runs the case's own fixture script
claude plugin eval . --scaffold --allow-tools Bash Edit
```

Granting `Bash` requires Claude Code's OS sandbox (on Linux: `bubblewrap` and
`socat`). Without it, grant only `Edit`: the reviewer then reviews by reading,
without running the tests.

### Measured: splitting the reviewer

Matt Pocock's `/code-review` splits review into a standards check and a spec
check, run as separate agents "so neither pollutes the other". We measured the
same split for Forge's reviewer on 2026-09-24: `spec-reviewer` (checklist items
spec match, tests, correctness) and `standards-reviewer` (build, commits, code
quality, security), each with its own lens and completion criterion, run in
parallel with a combined verdict. The variant lives on the
`experiment/split-reviewer` branch; the cases are identical apart from the
prompt naming the agents.

3 runs per case, Opus reviewers, `Edit` granted, no Bash (no sandbox on the
host):

| Case | Single reviewer | Split pair |
|------|-----------------|-----------|
| Spec violation caught | 3/3 | 3/3 |
| Both standards violations caught | 3/3 | 3/3 |
| Cost per review | ~$0.18 | ~$0.32 |
| Wall time per review | ~40 s | ~48 s |
| Clean phase approved | not measurable without Bash | not measurable without Bash |

The split catches nothing the single reviewer misses, at 1.8x the cost, so
Forge keeps one reviewer. The clean-phase case did its job anyway: both
variants refused to approve without having run the tests (the completion
criterion), and the first version of the fixture turned out to have a real
bug (it lowercased before filtering, so the Kelvin sign became `k`), which
both variants found. Re-run with `--allow-tools Bash Edit` on a host with the
sandbox to measure false positives; revisit the split when a case exists that
the single reviewer misses.

