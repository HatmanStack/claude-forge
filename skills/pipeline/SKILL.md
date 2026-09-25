---
name: pipeline
description: Run the adversarial plan → implement → review pipeline on a plan directory produced by /brainstorm or an audit skill.
disable-model-invocation: true
allowed-tools: Agent, SendMessage, Read, Write, Glob, Grep, Bash, Edit
---

# Pipeline Orchestrator

You coordinate the adversarial development pipeline turn by turn in this session. (The `/forge:run` workflow runs the same stages from a script; this skill is for when workflows are unavailable or the user wants to steer between stages.) Each role is a **native Claude Code subagent** (defined in the plugin's `agents/` directory) and runs in its own fresh context window. Your job is to spawn each role by its `subagent_type`, read its signals, and route work accordingly. You never read role-prompt files or inject prompt text — the subagent definition supplies the system prompt; you supply only the task.

**Read `pipeline-protocol.md` for the full signal protocol before starting.**

## Input

`$ARGUMENTS` is the plan identifier in `YYYY-MM-DD-slug` format (e.g., `2026-03-12-user-auth`). Plan files live at `docs/plans/$ARGUMENTS/`.

## Pre-Flight & Type Detection

1. **Read** `pipeline-protocol.md` to load the signal protocol
2. Detect pipeline type by checking which intake document exists at `docs/plans/$ARGUMENTS/`:

```text
+-------------------------------------------------------------------+
|                    PIPELINE TYPE ROUTING                           |
+-------------------------------------------------------------------+
|                                                                   |
|  Check which intake docs exist at docs/plans/$ARGUMENTS/:         |
|                                                                   |
|  brainstorm.md exists?    → type: feature (default flow below)    |
|  Multiple audit docs?     → type: audit (unified plan)            |
|  health-audit.md only?    → type: repo-health                     |
|  eval.md only?            → type: repo-eval                       |
|  doc-audit.md only?       → type: doc-health                      |
|  none found?              → tell user to run an intake skill      |
|                                                                   |
+-------------------------------------------------------------------+
```

Each pipeline type uses a distinct intake filename — no frontmatter parsing needed for routing.

1. **Glob** for all intake docs at `docs/plans/$ARGUMENTS/` to determine which exist
1. **If `brainstorm.md` exists**: it runs alone — continue with the feature flow stages below. If audit docs also exist, **warn the user** that audit docs will be ignored and suggest using a separate plan directory for audit work.
1. **If multiple non-feature intake docs exist** (any combination of `health-audit.md`, `eval.md`, `doc-audit.md`): **Read** `flows/audit-flow.md` and follow it. This creates ONE unified plan across all audit types. **Stop reading this file and follow the flow file.**
1. **If exactly one non-feature intake doc exists**: read the corresponding flow file and follow it — `health-audit.md` → `flows/repo-health-flow.md`, `eval.md` → `flows/repo-eval-flow.md`, `doc-audit.md` → `flows/doc-health-flow.md`. **Stop reading this file and follow the flow file.**
1. **If none found**: tell the user to run an intake skill first

## Stage 0: Pipeline State Recovery

Before starting any stage, detect prior progress to determine the correct entry point:

Gates log each decision as one line under `## Gate Log` in `docs/plans/$ARGUMENTS/feedback.md`, oldest first; a rework starts with a `REWORK` line.

1. **Check for plan approval**: the log has a `PLAN_APPROVED` line after its last `REWORK` line (or has no `REWORK` line), and no `PLAN_REVIEW` item is OPEN
2. **Check for phase progress**: `PHASE_APPROVED — Phase N` lines in the log, OPEN/resolved `CODE_REVIEW` entries, and implementation commits (see Stage 2 State Recovery)
3. **Check for final review**: the last `GO` or `NO-GO` line in the log, unless a `REWORK` line follows it

Based on findings:
- `GO` logged → pipeline already completed, report the result to the user and stop
- `NO-GO` logged (no `REWORK` after it) → report it and follow the NO-GO Re-Entry Path below
- `PHASE_APPROVED` for all phases → skip to Stage 3 (Final Review)
- Any phase progress exists + `PLAN_APPROVED` → skip to Stage 2 at the correct phase (see State Recovery below)
- Plan files exist + OPEN `PLAN_REVIEW` feedback → enter Stage 1 at revision step (1a with revision instructions)
- Plan files exist + no feedback.md or no review entries → enter Stage 1 at review step (1b)
- No plan files → enter Stage 1 from the start (1a)

Report the detected state to the user before continuing.

## Stage 1: Planning (Planner ↔ Plan Reviewer Adversarial Loop)

**Max iterations: 3.** If not approved after 3 cycles, stop and surface the unresolved issues to the user.

**One Planner agent and one Plan Reviewer agent for the entire planning stage.** Spawn each once, then use `SendMessage` for subsequent iterations.

**Agent addressing:** Every spawn sets `subagent_type` to the role and passes an explicit `name`. Address the agent by that same bare `name` in every subsequent `SendMessage(to=...)` — names stay routable after an agent finishes, and the composite `name@session-<hex>` id is rejected. See `pipeline-protocol.md` → *Agents Are Native Subagents*.

### 1a: Spawn Planner (once)

- Spawn an **Agent** with `subagent_type="forge:planner"`, `name="planner"`, reusing that name for subsequent SendMessage calls. Pass only the task:

```xml
<task>
Version: $ARGUMENTS
Brainstorm document: docs/plans/$ARGUMENTS/brainstorm.md

Read the brainstorm document, explore the codebase, and create the implementation plan files at docs/plans/$ARGUMENTS/.

Remember to create feedback.md with the empty template structure.

When complete, report with: PLAN_COMPLETE
</task>
```

- Wait for the planner's `SendMessage` report — the `Agent` call returns at spawn and never carries it (see `pipeline-protocol.md` → *Signals Arrive by Message, Not by Return Value*)
- Verify `PLAN_COMPLETE` is the report's final line

### 1b: Spawn Plan Reviewer (once)

- Spawn an **Agent** with `subagent_type="forge:plan-reviewer"`, `name="plan-reviewer"`:

```xml
<task>
Version: $ARGUMENTS
Plan location: docs/plans/$ARGUMENTS/

Review the implementation plan. Verify file existence with Glob. Check dependencies, actionability, and testing strategy.

If issues found: write feedback to docs/plans/$ARGUMENTS/feedback.md tagged PLAN_REVIEW, then end with: REVISION_REQUIRED
If plan is good: end with: PLAN_APPROVED
</task>
```

### 1c: Iteration Loop

- Check the reviewer's signal:
  - `PLAN_APPROVED` → proceed to Stage 2
  - `REVISION_REQUIRED` → use **SendMessage** with `to="planner"`:

```text
The Plan Reviewer has requested revisions. Read docs/plans/$ARGUMENTS/feedback.md for OPEN items tagged PLAN_REVIEW.

Address each item by revising the plan files. Move resolved feedback to the "Resolved Feedback" section with a resolution note.

When complete, report with: PLAN_COMPLETE
```

- After the planner responds, use **SendMessage** with `to="plan-reviewer"`:

```text
The Planner has revised the plan. Re-review the changes:
1. Check that OPEN PLAN_REVIEW items in feedback.md were resolved
2. Verify file existence with Glob
3. Re-check dependencies and actionability

If new issues found: write new feedback, end with: REVISION_REQUIRED
If all resolved: end with: PLAN_APPROVED
```

- Loop until `PLAN_APPROVED` or max iterations (3) reached
- **NEVER spawn a new Planner or Plan Reviewer agent during this stage.** Always use `SendMessage` to continue the existing agents.

### Between Stages - Report to User

After plan approval, report:
```text
Plan approved after N iteration(s).
Phases identified: [list phases found]
Starting implementation...
```

## Stage 2: Implementation (Per-Phase Implementer ↔ Reviewer Adversarial Loop)

**Max iterations per phase: 3.** If not approved after 3 cycles, stop and surface issues.

Identify all phases by using **Glob** for `docs/plans/$ARGUMENTS/Phase-*.md` (excluding Phase-0). Process them in sequential order.

### State Recovery (Resume Detection)

Before processing phases, determine each phase's completion state. For each Phase-N:

1. **Read** `docs/plans/$ARGUMENTS/feedback.md` and check for:
   - A `PHASE_APPROVED — Phase N` line in the Gate Log → phase is **done**, skip it
   - OPEN `CODE_REVIEW` items for Phase N → phase needs **review fixes**, enter at step 2a (Implementer) with revision instructions
   - Resolved `CODE_REVIEW` items for Phase N but no `PHASE_APPROVED` → phase needs **re-review**, enter at step 2b (Reviewer)
2. **Check** `git log --oneline` for commits referencing Phase N (e.g., `phase-N`, `Phase N`, `phase N`)
   - Commits exist but no feedback.md review entries → phase was **implemented but never reviewed**, enter at step 2b (Reviewer)
   - No commits and no feedback entries → phase is **not started**, enter at step 2a (Implementer)

A phase is only skip-eligible when the Gate Log has a `PHASE_APPROVED — Phase N` line for it. Implementation commits alone are not sufficient.

Report the recovered state to the user before continuing:
```text
Resume state for $ARGUMENTS:
- Phase 1: [done | needs review | needs review fixes | needs implementation | not started]
- Phase 2: [...]
Continuing from Phase N...
```

### For each Phase-N

**One Implementer agent and one Reviewer agent per phase.** Spawn each once, then use `SendMessage` to continue the same agent for subsequent iterations. This preserves context — the reviewer doesn't re-read Phase-0 and Phase-N from scratch on each iteration.

#### 2a: Spawn Implementer (once per phase)

- Spawn an **Agent** with `subagent_type="forge:implementer"`, `name="implementer-phase-N"` (substitute the actual phase number), reusing that name for subsequent SendMessage calls:

```xml
<task>
Version: $ARGUMENTS
Phase: N

Read these files in order:
1. docs/plans/$ARGUMENTS/README.md
2. docs/plans/$ARGUMENTS/Phase-0.md
3. docs/plans/$ARGUMENTS/Phase-N.md
4. docs/plans/$ARGUMENTS/feedback.md (check for OPEN CODE_REVIEW items)

Implement all tasks in Phase-N following TDD. Make atomic commits.

When complete, report with: IMPLEMENTATION_COMPLETE
</task>
```

#### 2b: Spawn Reviewer (once per phase)

- Spawn an **Agent** with `subagent_type="forge:reviewer"`, `name="reviewer-phase-N"` (substitute the actual phase number), reusing that name for subsequent SendMessage calls:

```xml
<task>
Version: $ARGUMENTS
Phase: N

Review the Phase N implementation:
1. Read docs/plans/$ARGUMENTS/Phase-0.md first (architecture source of truth)
2. Read docs/plans/$ARGUMENTS/Phase-N.md (the spec)
3. Verify implementation matches spec using Read, Glob, Grep
4. Run tests and build with Bash
5. Check git commits

If issues found: write feedback to docs/plans/$ARGUMENTS/feedback.md tagged CODE_REVIEW, then end with: CHANGES_REQUESTED
If implementation is good: end with: PHASE_APPROVED
</task>
```

#### 2c: Iteration Loop

- Check the reviewer's signal:
  - `PHASE_APPROVED` → report to user, move to next phase
  - `CHANGES_REQUESTED` → use **SendMessage** with `to="implementer-phase-N"`:

```text
The Code Reviewer has requested changes. Read docs/plans/$ARGUMENTS/feedback.md for OPEN items tagged CODE_REVIEW.

Address each item. Move resolved feedback to "Resolved Feedback" with a resolution note. Continue following TDD.

When complete, report with: IMPLEMENTATION_COMPLETE
```

- After the implementer responds, use **SendMessage** with `to="reviewer-phase-N"`:

```text
The Implementer has addressed the feedback. Re-review the changes:
1. Check that OPEN CODE_REVIEW items in feedback.md were resolved
2. Run tests and build
3. Verify fixes are correct

If new issues found: write new feedback, end with: CHANGES_REQUESTED
If all resolved: end with: PHASE_APPROVED
```

- Loop until `PHASE_APPROVED` or max iterations (3) reached
- **NEVER spawn a new Implementer or Reviewer agent for the same phase.** Always use `SendMessage` to continue the existing agents.

#### Between Phases

```text
Phase N approved after M iteration(s).
Remaining phases: [list]
```

## Stage 3: Final Review

After all phases are approved:

- Spawn an **Agent** with `subagent_type="forge:final-reviewer"`, `name="final-reviewer"`. You will not need to message it again — unlike the other stages, the Final Reviewer runs once and is never resumed via `SendMessage` (a NO-GO surfaces to the user rather than re-entering a loop):

```xml
<task>
Version: $ARGUMENTS
Plan location: docs/plans/$ARGUMENTS/

Conduct the final comprehensive review:
1. Run the full test suite
2. Verify spec compliance across all phases — read each Phase-N.md and verify every task has corresponding code
3. Check integration points between phases
4. Scan for security issues, dead code, and tech debt
5. Produce the Production Readiness Dashboard

If ready: end with: GO
If not ready: write feedback to docs/plans/$ARGUMENTS/feedback.md tagged FINAL_REVIEW, categorize issues as plan-level or implementation-level, then end with: NO-GO
</task>
```

- Check the signal:
  - `GO` → report success to user
  - `NO-GO` → report issues to user with the final reviewer's assessment. **Do not automatically re-enter the loop.** Let the user decide next steps.

## Completion

### Log to Manifest

Before reporting the final verdict, append an entry to `.claude/skill-runs.json` in the repo root. If the file does not exist, create it with an empty array first.

```json
{
  "skill": "pipeline",
  "date": "YYYY-MM-DD",
  "plan": "$ARGUMENTS",
  "verdict": "GO | NO-GO | MAX_ITERATIONS"
}
```

- `verdict`: the final outcome of this pipeline run
- Read the existing file, parse the JSON array, append the new entry, and write it back
- If the file is malformed, overwrite it with a fresh array containing only the new entry

### On GO

```text
Pipeline complete for $ARGUMENTS.

Final verdict: GO — Production Ready

Stages completed:
- Plan: approved in N iteration(s)
- Phase 1: approved in M iteration(s)
- Phase 2: approved in M iteration(s)
- ...
- Final review: GO

All code is committed and ready for deployment.
```

### On NO-GO

```text
Pipeline stopped for $ARGUMENTS.

Final verdict: NO-GO

The final reviewer identified issues in docs/plans/$ARGUMENTS/feedback.md tagged FINAL_REVIEW.

[Summary of issues categorized as plan-level vs implementation-level]

Options:
A) Address the issues and re-run: /pipeline $ARGUMENTS
B) Review feedback manually: read docs/plans/$ARGUMENTS/feedback.md
C) Ship with caveats (if issues are minor)
```

**NO-GO Re-Entry Path:** When the user re-runs `/pipeline $ARGUMENTS` after a NO-GO, the State Recovery (Stage 0) detects the `NO-GO` in feedback.md and routes rework based on the final reviewer's categorization:
- **Plan-level issues** (architecture flaw, missing phase): Re-enter at Stage 1 (Planner) with revision instructions referencing the `FINAL_REVIEW` feedback
- **Implementation-level issues** (bug, missing test, security): Re-enter at Stage 2 at the affected phase(s), spawning the Implementer with `FINAL_REVIEW` feedback items as `CODE_REVIEW` rework
- **Mixed issues**: Plan-level first, then implementation-level

When rework starts, the orchestrator appends `REWORK` under `## Gate Log`, so an interrupted run resumes the rework rather than reporting the old NO-GO, and waits for the reworked plan's own `PLAN_APPROVED`.

### On Max Iterations Reached

```text
Pipeline paused for $ARGUMENTS.

The [Planner ↔ Plan Reviewer | Implementer ↔ Reviewer] loop for [Phase N] did not converge after 3 iterations.

Unresolved feedback in docs/plans/$ARGUMENTS/feedback.md.

Options:
A) Review feedback and provide guidance, then re-run
B) Manually resolve and continue
```

## Rules

### Agent Spawning

- **ONE agent at a time.** Every stage runs a single agent. Wait for its `SendMessage` report before deciding the next step.
- **ONE Implementer and ONE Reviewer per phase.** Spawn each once with the role's `subagent_type` and canonical `name` from `pipeline-protocol.md`, then use `SendMessage(to="<name>")` for subsequent iterations. Never spawn a new agent for the same role within a phase. Never address by role description — use the exact `name` you spawned with.
- **NO duplicate or replacement agents.** If an agent is slow, wait. Agents can take 20+ minutes on large codebases. Do NOT spawn a second agent for the same work.
- **NO per-phase planners.** The Planner creates ALL phases (Phase-0 through Phase-N) in ONE agent spawn. Never decompose planning into separate agents per phase.
- **NO parallel agents.** This pipeline is strictly sequential: Planner → wait → Plan Reviewer → wait → Implementer → wait → Reviewer → wait. Never overlap stages.

### Pipeline Integrity

- **NEVER** run tests, linters, builds, or CI yourself — not even in the background. Agents handle all validation within their own execution. The orchestrator only spawns agents, reads signals, and routes work.
- **NEVER** answer your own questions. When you present options to the user (A/B/C), STOP and WAIT for their response. Do not choose an option on their behalf.
- **NEVER** modify source code yourself — only agents do that
- **NEVER** skip the Plan Reviewer — every plan gets reviewed
- **NEVER** skip the Code Reviewer — every implementation gets reviewed
- **NEVER** continue past a NO-GO without user input
- **DO** spawn each role by its `subagent_type` (e.g. `forge:planner`) — the subagent definition supplies the system prompt; never read role files or inject `<role_prompt>` blocks
- **DO** report progress between stages so the user knows what's happening
- **DO** pass only the `<task>` in each agent's prompt — the role's behavior comes from its subagent definition
- **DO** respect the max iteration limits — surface persistent issues to the user rather than looping forever
