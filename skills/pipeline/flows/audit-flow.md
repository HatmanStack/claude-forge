# Pipeline Flow: audit (Unified)

## Overview

When multiple intake docs exist, the pipeline creates ONE plan with phases tagged by implementer type. Each phase routes to the correct implementer/reviewer pair.

```text
+-----------+     +----------+     +--------------+     +-------------------+     +-------------------+     +-------------+
| All Audit | --> | Planner  | --> | Plan Reviewer| --> | Tagged Phases     | --> | Tagged Reviewers  | --> | Re-Evaluate |
| Docs      |     | (1 plan) |     |              |     | [HYGIENIST]       |     | health-reviewer   |     | + Re-Audit  |
|           |     |          |     |              |     | [FORTIFIER]       |     | health-reviewer   |     |             |
|           |     |          |     |              |     | [IMPLEMENTER]     |     | reviewer          |     |             |
|           |     |          |     |              |     | [DOC-ENGINEER]    |     | doc-reviewer      |     |             |
+-----------+     +----------+     +--------------+     +-------------------+     +-------------------+     +-------------+
                        ^                |                       ^                        |                        |
                        |  REVISION_     |                      |  CHANGES_              |                        |
                        +--REQUIRED------+                      +--REQUESTED-------------+                        |
                                                                                                                  |
                                                         +--------------------------------------------------------+
                                                         | Any gate not met? Loop back to Planner
                                                         +--------------------------------------------------------+
```

## Intake Documents

Multiple docs exist at `docs/plans/$ARGUMENTS/`:
- `health-audit.md` (if present) — tech debt findings
- `eval.md` (if present) — 12-pillar scores with remediation targets
- `doc-audit.md` (if present) — documentation drift findings

## Phase Tags and Role Routing

| Phase Tag | Implementer Role | Reviewer Role | Work Type |
|-----------|-----------------|---------------|-----------|
| `[HYGIENIST]` | `forge:health-hygienist` | `forge:health-reviewer` | Subtractive: delete dead code, remove unused deps, simplify |
| `[FORTIFIER]` | `forge:health-fortifier` | `forge:health-reviewer` | Additive: lint configs, CI, hooks, type strictness |
| `[IMPLEMENTER]` | `forge:implementer` | `forge:reviewer` | Code fixes: architecture, error handling, performance, testing |
| `[DOC-ENGINEER]` | `forge:doc-engineer` | `forge:doc-reviewer` | Doc fixes: delete stale, fix drift, add prevention |

## State Recovery (Resume Detection)

Before starting any stage, detect prior progress:

1. **Check feedback.md** for `VERIFIED` signal → pipeline already complete, report and stop
2. **Check for plan files**: Glob for `docs/plans/$ARGUMENTS/Phase-*.md`
3. **Check feedback.md** (if it exists):
   - `PHASE_APPROVED` for all phases → enter at Stage 3 (Verification)
   - `PLAN_APPROVED` with no phase progress → enter at Stage 2 (Implementation)
   - OPEN `CODE_REVIEW` items → enter at Stage 2 at the correct phase with revision instructions
   - OPEN `PLAN_REVIEW` items → enter at Stage 1 with revision instructions
4. **No plan files, no feedback.md** → enter at Stage 1 (first run)

Apply the same per-phase state recovery logic from the main SKILL.md (check `PHASE_APPROVED`, OPEN/resolved `CODE_REVIEW`, and git commits per phase).

If `docs/plans/$ARGUMENTS/feedback.md` does not exist, create it with the empty template from `pipeline-protocol.md` before proceeding to any stage.

Report detected state to the user before continuing.

## Pre-Flight: Agent Availability

All roles are native subagents discovered from the plugin's `agents/` directory (or `.claude/agents/` for a standalone install), so no role-file reading is required. If a needed `subagent_type` is unavailable, **stop and report** it.

## Critical Rule: No Evaluator/Auditor Agents During Planning or Implementation

Evaluator and auditor agents are **token-expensive**. They run exactly twice in the full lifecycle:

1. **Once during `/audit` intake** — produces the intake docs
2. **Never again** — Stage 3 (Verification) uses the existing code reviewer to verify findings, NOT the evaluator/auditor agents

**NEVER** re-run evaluator or auditor agents at any point during the pipeline. The planner, implementer, and verification reviewer work from the intake docs and feedback.md.

## Stage 1: Planning (Planner ↔ Plan Reviewer Adversarial Loop)

**Max iterations: 3.**

The planner reads ALL intake docs and creates ONE unified plan.

### 1a: Spawn Planner

**Agent addressing:** All spawns follow the convention in `pipeline-protocol.md` — pass an explicit `name` at spawn as a human-readable label, then **capture the returned `agentId`** and use that captured id in every subsequent `SendMessage(to=...)`. The `name` is not a routable address once the Agent call returns. Phase tags (`[HYGIENIST]`, `[FORTIFIER]`, etc.) select the `subagent_type` but do not change the label: phase N is always labeled `implementer-phase-N` / `reviewer-phase-N`.

- Spawn an **Agent** with `subagent_type="forge:planner"`, `name="planner"` (label only), and **capture the returned `agentId`** for subsequent SendMessage calls:

```text
<task>
Version: $ARGUMENTS

This is a UNIFIED AUDIT remediation plan. Multiple intake documents exist — read ALL of them:
- docs/plans/$ARGUMENTS/health-audit.md (if exists) — tech debt findings
- docs/plans/$ARGUMENTS/eval.md (if exists) — 12-pillar evaluation scores
- docs/plans/$ARGUMENTS/doc-audit.md (if exists) — documentation drift findings

Create ONE plan with phases sequenced in this order:
1. [HYGIENIST] phases FIRST — subtractive cleanup (dead code, unused deps, simplify)
2. [IMPLEMENTER] phases NEXT — code fixes (architecture, error handling, performance, testing)
3. [FORTIFIER] phases NEXT — additive guardrails (lint, CI, hooks, type safety)
4. [DOC-ENGINEER] phases LAST — documentation fixes and prevention tooling

Key constraints:
- Tag EVERY phase title with exactly one of: [HYGIENIST], [IMPLEMENTER], [FORTIFIER], [DOC-ENGINEER]
- The tag determines which implementer and reviewer handle that phase
- Cleanup before structural fixes before guardrails before docs
- Where findings overlap across audit types, consolidate into a single task
- Quick wins and CRITICAL findings should be in early phases
- Phase sizing: remediation phases are typically smaller than feature phases. Size to the work — a single-phase plan is fine if the scope fits. Do NOT pad phases to reach ~50k tokens.

Explore the codebase and create the plan files at docs/plans/$ARGUMENTS/.

When complete, end with: PLAN_COMPLETE
</task>
```

### 1a (Re-entry): Spawn Planner After Re-Evaluation

When looping back from Stage 3 (Verification) with unverified items, reuse the existing planner via `SendMessage(to=<captured planner agentId>, ...)` rather than spawning a new agent. If you no longer have the captured `agentId` (new session, missing scratch state), spawn a fresh planner with `subagent_type="forge:planner"`, `name="planner"` (label), and capture its new `agentId`:

```text
<task>
Version: $ARGUMENTS

Verification found unverified items. Read docs/plans/$ARGUMENTS/feedback.md for the UNVERIFIED findings.

Create a NEW remediation plan addressing ONLY the unverified items. Previous plan files may exist — create new Phase-N.md files starting after the last existing phase number.

Tag every phase with [HYGIENIST], [IMPLEMENTER], [FORTIFIER], or [DOC-ENGINEER].

When complete, end with: PLAN_COMPLETE
</task>
```

### 1b: Spawn Plan Reviewer

Standard plan review process — see main SKILL.md Stage 1b.

Loop until `PLAN_APPROVED` or max iterations.

## Stage 2: Implementation (Per-Phase Adversarial Loops)

**Max iterations per phase: 3.**

Identify all phases by Glob for `docs/plans/$ARGUMENTS/Phase-*.md` (excluding Phase-0). Process sequentially.

### Phase Tag Routing

For each phase, read the phase title to determine the tag, then spawn the correct implementer and reviewer:

**[HYGIENIST] phases:**
- Implementer: spawn subagent_type=forge:health-hygienist
- Reviewer: spawn subagent_type=forge:health-reviewer

**[FORTIFIER] phases:**
- Implementer: spawn subagent_type=forge:health-fortifier
- Reviewer: spawn subagent_type=forge:health-reviewer

**[IMPLEMENTER] phases:**
- Implementer: spawn subagent_type=forge:implementer
- Reviewer: spawn subagent_type=forge:reviewer

**[DOC-ENGINEER] phases:**
- Implementer: spawn subagent_type=forge:doc-engineer
- Reviewer: spawn subagent_type=forge:doc-reviewer

Agent spawn format is the same as main SKILL.md Stage 2, substituting the appropriate `subagent_type` per phase tag. Use `name="implementer-phase-N"` and `name="reviewer-phase-N"` as labels regardless of which `subagent_type` was spawned — the tag picks the `subagent_type`, not the label. **Capture the `agentId`** returned by each spawn and route every subsequent `SendMessage(to=...)` to that captured id, not to the name string.

Loop until `PHASE_APPROVED` or max iterations per phase.

Report between phases:
```text
Phase N [TAG] approved after M iteration(s).
Remaining phases: [list with tags]
```

## Stage 3: Verification

After all phases are `PHASE_APPROVED`, run a single verification agent that verifies the original findings from all intake docs. This is NOT a full re-evaluation — it's a targeted check using the existing code reviewer role.

### 3a: Spawn Verification Agent

- Spawn **one Agent** with `subagent_type="forge:reviewer"`, `name="verification-reviewer"` (label only), and **capture the returned `agentId`** in case a re-entry SendMessage is needed:

```text
<task>
Version: $ARGUMENTS

This is a VERIFICATION pass after remediation. You are NOT doing a full code review — you are verifying that specific findings from the original audit were addressed.

Read the original intake docs to get the list of findings:
- docs/plans/$ARGUMENTS/eval.md (if exists) — check REMEDIATION TARGETS
- docs/plans/$ARGUMENTS/health-audit.md (if exists) — check CRITICAL and HIGH findings
- docs/plans/$ARGUMENTS/doc-audit.md (if exists) — check DRIFT, STALE, and BROKEN LINK findings

For each finding:
1. Read the specific file:line referenced in the finding
2. Verify the issue was addressed (Glob/Grep/Read)
3. Run tests if the finding was about test coverage or behavior

Report which findings are VERIFIED (fixed) vs UNVERIFIED (still present).

Also run the full test suite to catch regressions.

If all findings verified and tests pass: end with VERIFIED
If any findings unverified or tests fail: list the unverified items, then end with UNVERIFIED
</task>
```

### 3b: Persist and Assess Results

The **orchestrator** must write the verification result to feedback.md **before** reporting to the user. This ensures state recovery can detect completion if interrupted.

1. If agent returned `VERIFIED`: **Edit** feedback.md to append `VERIFIED` under a `## Verification` section
2. If agent returned `UNVERIFIED`: **Edit** feedback.md to append `UNVERIFIED` with the list of unverified items under a `## Verification` section

Then assess:
- If `VERIFIED` → report success
- If `UNVERIFIED` → the orchestrator reads the unverified items and decides:
  - If minor (< 3 items): report to user with specific items, let them decide
  - If significant: loop back to Stage 1 with the unverified items as new targets

**Max verification cycles: 2.** If items remain unverified after 2 cycles, stop and surface to user.

### If verified: Report success

```text
Pipeline complete for $ARGUMENTS.

Final verdict: VERIFIED

Verification checked [N] findings from original audit:
- [X] verified (fixed)
- [Y] unverified (if any, listed below)

Tests: [all passing / N failures]

All remediation is committed and verified.
```

### If unverified: Report to user

**STOP HERE. Present these options to the user and WAIT for their response. Do NOT choose an option yourself.**

```text
Pipeline paused for $ARGUMENTS.

Verification found [Y] unverified items:
- [finding 1 — file:line — still present because...]
- [finding 2 — ...]

Options:
A) Re-enter planning for unverified items: /pipeline $ARGUMENTS
B) Review manually and decide
C) Accept as-is
```
