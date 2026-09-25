# Pipeline Protocol

Shared contract defining stage sequencing, signals, and communication channels for the adversarial review pipeline. It binds both runners: the `/forge:pipeline` skill, which orchestrates turn by turn in the session, and the `/forge:run` workflow (`workflows/run.js`), which encodes the same stages in a script.

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

When you spawn an `Agent`, set `subagent_type` to the role's type and `name` to the canonical name below — you will reuse it to message the agent later. The subagent definition supplies the system prompt; your `Agent` `prompt` carries only the task.

| Role | `subagent_type` | `name` |
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

### Addressing for Iteration: use the bare `name`

Spawn each role with an explicit `name`, and address it by that **same bare name** in every subsequent `SendMessage`. A name keeps working after an agent has finished — a send resumes it from its transcript.

Do **not** pass the composite id from the spawn result (the `name@session-<hex>` form). `SendMessage` rejects it:

```text
to must be a bare teammate name — there is only one team per session
```

There is one team per session, so names are unique within it and are the routable address. Use the raw `agentId` only when an agent has no name at all, or when a newer agent has taken the name (latest wins).

**Names do not survive a session restart.** A restart kills running agents; `SendMessage` then returns `No agent named '<name>' is reachable`. That is a dead agent, not a bad address — spawn a fresh agent of the same `subagent_type` with the same `name`. Pipeline state lives in the plan files and `feedback.md`, so a lost agent costs context, not progress.

> Team coordination tools (`SendMessage` and task tools) are always available to a teammate even when its frontmatter `tools` list restricts other tools — so a read-only reviewer can still be messaged and can still reply.

### Worked Example

```text
# Spawn planner by type, with a name you will reuse
Agent(subagent_type="forge:planner", name="planner", prompt="<task>...</task>")
→ planner reports PLAN_COMPLETE via SendMessage(to="main")

# Spawn plan reviewer by type
Agent(subagent_type="forge:plan-reviewer", name="plan-reviewer", prompt="<task>...</task>")
→ reviewer reports REVISION_REQUIRED via SendMessage(to="main")

# Revise — SAME planner, addressed by its bare name (no re-spawn)
SendMessage(to="planner", message="Read feedback.md OPEN PLAN_REVIEW items...")
→ planner reports PLAN_COMPLETE

# Re-review — SAME plan-reviewer, by its bare name
SendMessage(to="plan-reviewer", message="Re-review the revised plan...")
→ reviewer reports PLAN_APPROVED
```

**Never** pass the composite `name@session-<hex>` id — `SendMessage` rejects it outright. Address roles by the bare `name` you spawned them with.

### Signals Arrive by Message, Not by Return Value

A role reports on one of two channels; each agent's *Reporting Results* section covers both:

- **Under `/forge:run`**, the role calls `StructuredOutput` with a typed report whose `signal` field is the verdict. The script branches on that field, so a signal can't be misread, skipped, or forged by the orchestrator.
- **Under `/forge:pipeline`**, the role runs as a teammate. Its plain-text output is **discarded**; only a `SendMessage(to="main")` call reaches the orchestrator.

Consequences for the `/forge:pipeline` orchestrator:

- The `Agent` call returns as soon as the agent is spawned. It does **not** block, and its result never contains the agent's report.
- A bare `idle_notification` with no accompanying message means the agent finished without reporting. Ask it to resend via `SendMessage(to="main")` rather than inferring its verdict.
- **Never manufacture a signal you did not receive.** A gate's report is the verdict; the approval lines in `feedback.md` exist so a later run can resume, not to stand in for a report you are waiting on.

## Communication Channel: feedback.md

All review feedback lives in `docs/plans/<plan_id>/feedback.md`. Plan documents are **never mutated** by reviewers.

### feedback.md Template

Create a missing `feedback.md` from this template, exactly. Its Gate Log starts empty: any line in it is a recorded decision.

```markdown
# Feedback Log

## Active Feedback

## Resolved Feedback

## Gate Log
```

### Example: a populated feedback.md

Not a template; copying it would record decisions that were never made.

```markdown
# Feedback Log

## Active Feedback

### CODE_REVIEW - Iteration 1 - Phase 2, Task 3

> **Consider:** ...
> **Think about:** ...
> **Reflect:** ...

**Status:** OPEN

---

## Resolved Feedback

### PLAN_REVIEW - Iteration 1 - Phase 1, Task 2

> **Consider:** ...

**Status:** RESOLVED
**Resolution:** Brief description of how it was addressed

---

## Verification

- [unverified finding — file:line — why it is still present]

## Gate Log

PLAN_APPROVED
PHASE_APPROVED — Phase 1
UNVERIFIED
REWORK
```

### Rules

- **Reviewers** append new feedback under "Active Feedback" with status OPEN
- **Generators** (Planner/Implementer) move resolved items to "Resolved Feedback" with a resolution note
- Tag feedback with `PLAN_REVIEW` or `CODE_REVIEW` so the correct generator knows which items are theirs
- Reference specific files, line numbers, and test names
- Use rhetorical questions (Consider / Think about / Reflect) -- don't provide answers
- **Gates log decisions.** `## Gate Log` is an ordered, append-only log, one decision per line: the Plan Reviewer logs `PLAN_APPROVED`, a phase reviewer `PHASE_APPROVED — Phase N`, the Final Reviewer `GO` or `NO-GO`, the verifier `VERIFIED` or `UNVERIFIED` (listing unverified findings under `## Verification`). A rework begins by logging `REWORK`
- **Rework adds phases; it never reopens one.** After a NO-GO or UNVERIFIED, rework starts by logging `REWORK`. The Planner fixes plan-level issues in the existing phase files and adds new Phase-N files for implementation fixes; the Plan Reviewer approves the revised plan; only the new phases are implemented and reviewed. Approved phases stay approved, so the log stays append-only and every line in it stays true
- **Resume reads the log in order.** The current verdict is the last `GO`/`NO-GO`/`VERIFIED`/`UNVERIFIED` line unless a `REWORK` line follows it; the plan is approved only if a `PLAN_APPROVED` line follows the last `REWORK`; a phase is done when its `PHASE_APPROVED — Phase N` line is present. A decision that isn't logged is made again

## File Ownership

| File          | Created By | Edited By                                  | Purpose                           |
|---------------|------------|--------------------------------------------|------------------------------------|
| README.md     | Planner    | Planner                                    | Overview and navigation            |
| Phase-0.md    | Planner    | Planner                                    | Architecture decisions (source of truth) |
| Phase-N.md    | Planner    | Planner, Implementer (checkboxes only)     | Implementation instructions        |
| feedback.md   | Planner    | Gates (review feedback, approvals, verification), generators (resolving items) | Review feedback, approvals, verification results |
| eval.md       | Intake skill | Calibration step (repo-eval) appends `## Calibration`; otherwise read only | Repo evaluation scores and targets |
| health-audit.md | Intake skill | Orchestrator (read only during pipeline) | Tech debt findings                       |
| doc-audit.md  | Intake skill | Orchestrator (read only during pipeline)   | Documentation drift findings               |
