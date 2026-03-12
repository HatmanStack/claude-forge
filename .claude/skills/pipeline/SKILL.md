---
name: pipeline
description: Run the adversarial plan-implement-review pipeline. Spawns agents for each role with their own context windows. Use after /brainstorm has produced a design spec.
allowed-tools: Agent, Read, Glob, Grep, Bash, Edit
---

# Pipeline Orchestrator

You coordinate the adversarial development pipeline. Each role runs as a separate agent with a fresh context window. Your job is to spawn agents, read their signals, and route work accordingly.

**Read `pipeline-protocol.md` for the full signal protocol before starting.**

## Input

`$ARGUMENTS` is the plan identifier in `YYYY-MM-DD-feature-slug` format (e.g., `2026-03-12-user-auth`). The brainstorm doc and plan files live at `docs/plans/$ARGUMENTS/`.

## Pre-Flight

1. Verify `docs/plans/$ARGUMENTS/brainstorm.md` exists — if not, tell the user to run `/brainstorm` first
2. **Read** the brainstorm doc to understand the feature scope
3. **Read** `pipeline-protocol.md` to load the signal protocol

## Stage 1: Planning (Planner ↔ Plan Reviewer GAN Loop)

**Max iterations: 3.** If not approved after 3 cycles, stop and surface the unresolved issues to the user.

### 1a: Spawn Planner

- **Read** `planner.md` to load the role prompt
- Spawn an **Agent** (subagent_type: general-purpose) with the following prompt structure:

```
<role_prompt>
[Contents of planner.md]
</role_prompt>

<task>
Version: $ARGUMENTS
Brainstorm document: docs/plans/$ARGUMENTS/brainstorm.md

Read the brainstorm document, explore the codebase, and create the implementation plan files at docs/plans/$ARGUMENTS/.

Remember to create feedback.md with the empty template structure.

When complete, end your response with: PLAN_COMPLETE
</task>
```

- Wait for the agent to complete
- Verify `PLAN_COMPLETE` is in the result

### 1b: Spawn Plan Reviewer

- **Read** `plan_reviewer.md` to load the role prompt
- Spawn an **Agent** with:

```
<role_prompt>
[Contents of plan_reviewer.md]
</role_prompt>

<task>
Version: $ARGUMENTS
Plan location: docs/plans/$ARGUMENTS/

Review the implementation plan. Verify file existence with Glob. Check dependencies, actionability, and testing strategy.

If issues found: write feedback to docs/plans/$ARGUMENTS/feedback.md tagged PLAN_REVIEW, then end with: REVISION_REQUIRED
If plan is good: end with: PLAN_APPROVED
</task>
```

- Check the signal in the result:
  - `PLAN_APPROVED` → proceed to Stage 2
  - `REVISION_REQUIRED` → re-spawn Planner (1a) with revision instructions:

```
<role_prompt>
[Contents of planner.md]
</role_prompt>

<task>
Version: $ARGUMENTS

The Plan Reviewer has requested revisions. Read docs/plans/$ARGUMENTS/feedback.md for OPEN items tagged PLAN_REVIEW.

Address each item by revising the plan files. Move resolved feedback to the "Resolved Feedback" section with a resolution note.

When complete, end your response with: PLAN_COMPLETE
</task>
```

- Loop until `PLAN_APPROVED` or max iterations reached

### Between Stages: Report to User

After plan approval, report:
```
Plan approved after N iteration(s).
Phases identified: [list phases found]
Starting implementation...
```

## Stage 2: Implementation (Per-Phase Implementer ↔ Reviewer GAN Loop)

**Max iterations per phase: 3.** If not approved after 3 cycles, stop and surface issues.

Identify all phases by using **Glob** for `docs/plans/$ARGUMENTS/Phase-*.md` (excluding Phase-0). Process them in sequential order.

### For each Phase-N:

#### 2a: Spawn Implementer

- **Read** `implementer.md` to load the role prompt
- Spawn an **Agent** with:

```
<role_prompt>
[Contents of implementer.md]
</role_prompt>

<task>
Version: $ARGUMENTS
Phase: N

Read these files in order:
1. docs/plans/$ARGUMENTS/README.md
2. docs/plans/$ARGUMENTS/Phase-0.md
3. docs/plans/$ARGUMENTS/Phase-N.md
4. docs/plans/$ARGUMENTS/feedback.md (check for OPEN CODE_REVIEW items)

Implement all tasks in Phase-N following TDD. Make atomic commits.

When complete, end your response with: IMPLEMENTATION_COMPLETE
</task>
```

#### 2b: Spawn Reviewer

- **Read** `reviewer.md` to load the role prompt
- Spawn an **Agent** with:

```
<role_prompt>
[Contents of reviewer.md]
</role_prompt>

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

- Check the signal:
  - `PHASE_APPROVED` → report to user, move to next phase
  - `CHANGES_REQUESTED` → re-spawn Implementer (2a) with:

```
<role_prompt>
[Contents of implementer.md]
</role_prompt>

<task>
Version: $ARGUMENTS
Phase: N

The Code Reviewer has requested changes. Read docs/plans/$ARGUMENTS/feedback.md for OPEN items tagged CODE_REVIEW.

Address each item. Move resolved feedback to "Resolved Feedback" with a resolution note. Continue following TDD.

When complete, end your response with: IMPLEMENTATION_COMPLETE
</task>
```

- Loop until `PHASE_APPROVED` or max iterations reached

#### Between Phases: Report

```
Phase N approved after M iteration(s).
Remaining phases: [list]
```

## Stage 3: Final Review

After all phases are approved:

- **Read** `final_reviewer.md` to load the role prompt
- Spawn an **Agent** with:

```
<role_prompt>
[Contents of final_reviewer.md]
</role_prompt>

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

### On GO:

```
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

### On NO-GO:

```
Pipeline stopped for $ARGUMENTS.

Final verdict: NO-GO

The final reviewer identified issues in docs/plans/$ARGUMENTS/feedback.md tagged FINAL_REVIEW.

[Summary of issues categorized as plan-level vs implementation-level]

Options:
A) Address the issues and re-run: /pipeline $ARGUMENTS
B) Review feedback manually: read docs/plans/$ARGUMENTS/feedback.md
C) Ship with caveats (if issues are minor)
```

### On Max Iterations Reached:

```
Pipeline paused for $ARGUMENTS.

The [Planner ↔ Plan Reviewer | Implementer ↔ Reviewer] loop for [Phase N] did not converge after 3 iterations.

Unresolved feedback in docs/plans/$ARGUMENTS/feedback.md.

Options:
A) Review feedback and provide guidance, then re-run
B) Manually resolve and continue
```

## Rules

- **NEVER** modify source code yourself — only agents do that
- **NEVER** skip the Plan Reviewer — every plan gets reviewed
- **NEVER** skip the Code Reviewer — every implementation gets reviewed
- **NEVER** continue past a NO-GO without user input
- **DO** read each role prompt file fresh before spawning — don't cache from memory
- **DO** report progress between stages so the user knows what's happening
- **DO** include the full role prompt contents in each agent's prompt (the agent has no access to the skill directory files)
- **DO** respect the max iteration limits — surface persistent issues to the user rather than looping forever
