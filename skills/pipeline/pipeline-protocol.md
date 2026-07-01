# Pipeline Protocol

Shared contract defining stage sequencing, signals, and communication channels for the adversarial review pipeline. All role documents reference this protocol.

## Stage Sequence

```text
+----------+     +--------------+     +-------------+     +----------+     +----------------+
| Planner  | --> | Plan Reviewer| --> | Implementer | --> | Reviewer | --> | Final Reviewer |
+----------+     +--------------+     +-------------+     +----------+     +----------------+
     ^                 |                    ^                   |                   |
     |  REVISION_      |                   |  CHANGES_         |                   |
     +--REQUIRED-------+                   +--REQUESTED--------+                   |
                                                                                   |
     ^                                     ^                                       |
     |                                     |          NO-GO                         |
     +-------------------------------------+---------------------------------------+
```

## Signals

| Signal                  | Emitted By      | Triggers                                 | Action                                                        |
|-------------------------|-----------------|------------------------------------------|---------------------------------------------------------------|
| PLAN_COMPLETE           | Planner         | Plan Reviewer                            | Review plan files and verify against codebase                 |
| REVISION_REQUIRED       | Plan Reviewer   | Planner                                  | Check feedback.md, revise plan, re-emit PLAN_COMPLETE         |
| PLAN_APPROVED           | Plan Reviewer   | Implementer                              | Begin phase implementation                                    |
| IMPLEMENTATION_COMPLETE | Implementer     | Reviewer                                 | Review code against plan                                      |
| CHANGES_REQUESTED       | Reviewer        | Implementer                              | Check feedback.md, fix issues, re-emit IMPLEMENTATION_COMPLETE|
| PHASE_APPROVED          | Reviewer        | Next phase Implementer or Final Reviewer | Start next phase or final review                              |
| GO                      | Final Reviewer  | Deploy pipeline                          | Production ready                                              |
| NO-GO                   | Final Reviewer  | Planner or Implementer                   | Check feedback.md for scope of rework                         |
| VERIFIED                | Verification Reviewer | Pipeline complete                  | All findings from intake docs confirmed addressed             |
| UNVERIFIED              | Verification Reviewer | Planner (re-entry)               | Unverified items listed, orchestrator decides next step       |
| EVAL_HIRE_COMPLETE      | Eval Hire agent | Intake orchestrator                      | Hire evaluation finished (intake only)                        |
| EVAL_STRESS_COMPLETE    | Eval Stress agent | Intake orchestrator                    | Stress evaluation finished (intake only)                      |
| EVAL_DAY2_COMPLETE      | Eval Day2 agent | Intake orchestrator                      | Day 2 evaluation finished (intake only)                       |
| AUDIT_COMPLETE          | Health Auditor  | Intake orchestrator                      | Health audit finished (intake only)                           |
| DOC_AUDIT_COMPLETE      | Doc Auditor     | Intake orchestrator                      | Doc audit finished (intake only)                              |

## Agents Are Native Subagents

Every role in this pipeline is a **native Claude Code subagent** defined under the plugin's `agents/` directory (e.g. `agents/planner.md`). The role prompt lives in that file's body and its tool/model constraints live in its YAML frontmatter. The orchestrator does **not** read role files or inject `<role_prompt>` blocks — it spawns a role by its **subagent type** and passes only the `<task>` for that invocation.

### Spawning a Role: `subagent_type`

When you spawn an `Agent`, set `subagent_type` to the role's type and `name` to a human-readable label. The subagent definition supplies the system prompt; your `Agent` `prompt` carries only the task.

| Role | `subagent_type` | `name` (label only) |
|------|-----------------|---------------------|
| Planner | `forge:planner` | `planner` |
| Plan Reviewer | `forge:plan-reviewer` | `plan-reviewer` |
| Implementer | `forge:implementer` | `implementer-phase-N` |
| Code Reviewer | `forge:reviewer` | `reviewer-phase-N` |
| Final Reviewer | `forge:final-reviewer` | `final-reviewer` |
| Health Hygienist | `forge:health-hygienist` | `implementer-phase-N` |
| Health Fortifier | `forge:health-fortifier` | `implementer-phase-N` |
| Health Reviewer | `forge:health-reviewer` | `reviewer-phase-N` |
| Doc Engineer | `forge:doc-engineer` | `implementer-phase-N` |
| Doc Reviewer | `forge:doc-reviewer` | `reviewer-phase-N` |
| Verification Reviewer | `forge:reviewer` | `verification-reviewer` |
| Eval — Pragmatist | `forge:eval-hire` | `eval-hire` |
| Eval — Oncall | `forge:eval-stress` | `eval-stress` |
| Eval — Team Lead | `forge:eval-day2` | `eval-day2` |
| Health Auditor | `forge:health-auditor` | `health-auditor` |
| Doc Auditor | `forge:doc-auditor` | `doc-auditor` |

The phase tag (`[HYGIENIST]`, `[FORTIFIER]`, `[IMPLEMENTER]`, `[DOC-ENGINEER]`) selects which `subagent_type` to spawn for that phase — it does **not** change the label. Phase 3 tagged `[HYGIENIST]` spawns `forge:health-hygienist` / `forge:health-reviewer` but is still labeled `implementer-phase-3` / `reviewer-phase-3`.

> **Standalone install:** the `forge:` prefix is the plugin scope. If Forge was copied directly into a project (`agents/` → `.claude/agents/`), the same roles are addressed without the prefix — `planner` instead of `forge:planner`. Use whichever form resolves in your install.

### Addressing for Iteration: capture the `agentId`

This pipeline is **sequential**: it spawns each role in the foreground and **waits for it to return** before doing anything else. Live, concurrent teammates can be addressed by `name`, but a role that has already returned is a *completed* agent — and a completed agent is resumed by its **`agentId`**, not its name. The Agent tool's own result says so explicitly (e.g. *"use SendMessage with to: '<agentId>' to continue this agent"*).

So here the `name` is a label only (traces, transcripts, feedback.md references). Once an `Agent` call returns, capture the `agentId` (a 16-char hex string) from its result metadata and use that as the `to` for every subsequent `SendMessage`. Addressing a returned role by its `name` may collide, silently re-spawn a fresh agent, or fail.

**Orchestrator responsibility:** every time you spawn a role you may continue later (planner, plan reviewer, implementer, reviewer, verification reviewer), record the returned `agentId` in scratch state for the next `SendMessage`.

> Team coordination tools (`SendMessage` and task tools) are always available to a teammate even when its frontmatter `tools` list restricts other tools — so a read-only reviewer can still be messaged and can still reply.

### Worked Example

```text
# Spawn planner by type — capture the agentId from the result metadata
result = Agent(subagent_type="forge:planner", name="planner", prompt="<task>...</task>")
planner_id = result.agentId        # e.g. "a1b2c3d4e5f6a7b8" — record this
→ planner finishes with PLAN_COMPLETE

# Spawn plan reviewer by type — capture its agentId too
result = Agent(subagent_type="forge:plan-reviewer", name="plan-reviewer", prompt="<task>...</task>")
plan_reviewer_id = result.agentId
→ reviewer finishes with REVISION_REQUIRED

# Revise — SAME planner, addressed by the captured agentId (no re-spawn)
SendMessage(to=planner_id, message="Read feedback.md OPEN PLAN_REVIEW items...")
→ planner finishes with PLAN_COMPLETE

# Re-review — SAME plan-reviewer, by its captured agentId
SendMessage(to=plan_reviewer_id, message="Re-review the revised plan...")
→ reviewer finishes with PLAN_APPROVED
```

**Never** `SendMessage(to="planner")` or `SendMessage(to="<any name string>")` — the name is a label, not a routable address. Always use the captured `agentId`. If you lost the id (new session, missing scratch state), spawn a fresh agent of the same `subagent_type` with the same `name` label rather than guessing.

## Communication Channel: feedback.md

All review feedback lives in `docs/plans/<plan_id>/feedback.md`. Plan documents are **never mutated** by reviewers.

### feedback.md Structure

```markdown
# Feedback Log

## Active Feedback

### [PLAN_REVIEW | CODE_REVIEW] - Iteration N - Phase X, Task Y

> **Consider:** ...
> **Think about:** ...
> **Reflect:** ...

**Status:** OPEN

---

## Resolved Feedback

### [PLAN_REVIEW | CODE_REVIEW] - Iteration N - Phase X, Task Y

> **Consider:** ...

**Status:** RESOLVED
**Resolution:** Brief description of how it was addressed

---
```

### Rules

- **Reviewers** append new feedback under "Active Feedback" with status OPEN
- **Generators** (Planner/Implementer) move resolved items to "Resolved Feedback" with a resolution note
- Tag feedback with `PLAN_REVIEW` or `CODE_REVIEW` so the correct generator knows which items are theirs
- Reference specific files, line numbers, and test names
- Use rhetorical questions (Consider / Think about / Reflect) -- don't provide answers

## File Ownership

| File          | Created By | Edited By                                  | Purpose                           |
|---------------|------------|--------------------------------------------|------------------------------------|
| README.md     | Planner    | Planner                                    | Overview and navigation            |
| Phase-0.md    | Planner    | Planner                                    | Architecture decisions (source of truth) |
| Phase-N.md    | Planner    | Planner, Implementer (checkboxes only)     | Implementation instructions        |
| feedback.md   | Planner    | Plan Reviewer, Reviewer, Orchestrator      | All review feedback + verification results |
| eval.md       | Intake skill | Orchestrator (read only during pipeline) | Repo evaluation scores and targets         |
| health-audit.md | Intake skill | Orchestrator (read only during pipeline) | Tech debt findings                       |
| doc-audit.md  | Intake skill | Orchestrator (read only during pipeline)   | Documentation drift findings               |
