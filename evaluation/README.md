# Evaluation

How we know the Forge team still behaves as its prompts, tools, and the
orchestrator keep changing. Structured as an evaluation pyramid — fast and
deterministic at the base, realistic at the top.

| Tier | What | Where | Cadence |
|------|------|-------|---------|
| **A — Contracts** | Deterministic structural checks (frontmatter, tool policy, wiring, manifests) | `tier_a_contracts/` | Every push / PR |
| **B — Single agent** | Rubric checks of one agent against fixtures (the agent's own reviewer is its rubric) | _planned_ | Nightly (LLM-costed) |
| **C — Trajectory** | Governance-signal order validators (provenance, gate order, no skipped review) | `tier_c_trajectory/` + `check_run.py` | Synthetic per-PR; real runs nightly |
| **D — Live traces** | OpenTelemetry → Jaeger, plus `security:dp{1..5}.*` spans | `../hooks/trace_subagents.py` | On real runs |

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

No network, no LLM, no API keys — Tiers A and C are pure-stdlib + pytest.

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
