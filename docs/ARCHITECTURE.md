# Architecture

## The GAN Analogy

Claude Forge adapts the adversarial feedback loop from GAN architecture. In ML, GANs pit a generator against a discriminator, where the generator creates content, the discriminator evaluates it, and iterative feedback drives both to improve.

Claude Forge borrows that generator-discriminator structure for code:

```
+----------+                    +--------------+
| Generator|  -- artifact -->   | Discriminator|
| (Planner,|  <-- feedback --   | (Plan Review,|
|  Implmtr)|                    |  Code Review)|
+----------+                    +--------------+
```

The key insight: **each agent runs in its own context window**. The Plan Reviewer has never seen the Planner's reasoning process — only the output. The Code Reviewer has never seen the Implementer's struggles — only the code. Fresh context means fresh eyes.

## Native Subagents

Every role is a **native Claude Code subagent**, defined as a Markdown file in the plugin's `agents/` directory (auto-discovered and scoped as `forge:<name>`). The file body is the role's system prompt; its YAML frontmatter declares the tools and model it may use. The orchestrator spawns a role by its `subagent_type` and passes only the per-invocation task — it never reads a role file or injects a `<role_prompt>` block. This is what makes the team a *pure* Claude Code team rather than ad-hoc prompts handed to a generic agent.

Every role pins its `model`, so the team never silently inherits whatever model the session runs on. Discriminators (the four reviewers, the plan reviewer, the final reviewer) and the Planner run on `opus`: a gate must be at least as strong as the work it judges, and the plan is the highest-leverage artifact. Code generators and the read-only assessors run on `sonnet`; the parallel assessor fan-out is where cost multiplies. Tier A enforces the policy (`test_model_pinned_per_role_class`).

Tool access is gated per role in frontmatter, which turns the pipeline's safety conventions into structural guarantees:

| Role class | Tools | Why |
|------------|-------|-----|
| Generators (Planner, Implementer, Hygienist, Fortifier, Doc Engineer) | `Read, Write, Edit, Glob, Grep, Bash` | They produce and modify code and plans |
| Discriminators (Plan/Code/Final/Health/Doc Reviewers) | `Read, Glob, Grep, Bash, Edit` | Read-only over source; `Edit` is for `feedback.md` only |
| Assessors (Eval lenses, Health/Doc Auditors) | `Read, Glob, Grep, Bash` | Strictly read-only — the orchestrator writes the intake docs |

No role is granted the `Agent` tool, so no role can spawn agents of its own: all routing stays with the orchestrator. Team coordination tools (`SendMessage` and task tools) are always available to a spawned teammate regardless of its `tools` list, so a read-only reviewer can still be messaged for the next iteration and still reply with its signal.

## Two Runners

The same stages, roles, and plan files run under two orchestrators. They differ in who holds the plan.

| | `/forge:run` (workflow) | `/forge:pipeline` (skill) |
|---|---|---|
| Orchestrator | `workflows/run.js`, executed by the workflow runtime | The main session, following `skills/pipeline/` prose |
| Loop limits, routing, resume | Code | The model re-decides each turn |
| Role reports | `StructuredOutput`: a typed object; the verdict is a `signal` field | `SendMessage(to="main")`: text ending in the signal |
| Iterations | A fresh agent per iteration, re-reading the plan files | The same agent, continued with `SendMessage` |
| Your session | Free; the run is in the background (`/workflows`) | Occupied; every result passes through its context |
| Decisions | Ends with a verdict (`GO`, `NO-GO`, `VERIFIED`, `UNVERIFIED`, `MAX_ITERATIONS`) | Stops and asks at NO-GO or unverified findings |
| Requires | Dynamic workflows | `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` |

Moving the orchestration into code removes a class of failures rather than guarding against them. A skill orchestrator can misread a signal, skip a gate, or lose count of iterations. In the workflow, the verdict is a schema field the script branches on, and a gate that didn't run can't produce one. The cost is continuity: a workflow agent is one-shot, so a reviewer re-reads Phase-0 and the phase spec every iteration instead of remembering them. Pipeline state already lives in files, so that costs tokens, not correctness.

Before planning, `/forge:run` spends one cheap read-only agent reporting the plan's state (which the script cannot read itself). It writes nothing under `.claude/`: Claude Code protects that directory, and a background agent can't ask for permission. The run's durable record is the plan directory (`## Approvals` and `## Verification` in `feedback.md`) and the verdict it returns. Every role agent is spawned with the model its frontmatter pins; a Tier A contract keeps the script's pins equal to the frontmatter, and a Tier C suite runs the script against scripted replies to check gate order, loop limits, resume entry points, and phase routing.

## Signal Protocol

Agents communicate through signals routed by the orchestrator. Under `/forge:run` a signal is the `signal` field of the role's typed report; under `/forge:pipeline` it is the final line of the role's `SendMessage` report.

| Signal | From | To | Meaning |
|--------|------|----|---------|
| `PLAN_COMPLETE` | Planner | Plan Reviewer | Plan ready for review |
| `REVISION_REQUIRED` | Plan Reviewer | Planner | Issues found |
| `PLAN_APPROVED` | Plan Reviewer | Implementer | Plan is sound |
| `IMPLEMENTATION_COMPLETE` | Implementer | Reviewer | Phase code ready |
| `CHANGES_REQUESTED` | Reviewer | Implementer | Issues found |
| `PHASE_APPROVED` | Reviewer | Next phase / Final | Phase is solid |
| `GO` | Final Reviewer | Done | Production ready |
| `NO-GO` | Final Reviewer | Planner / Implementer | Categorized rework |
| `VERIFIED` / `UNVERIFIED` | Verification reviewer | Done / you (or re-plan) | Audit findings fixed, or which are not |

Non-feature pipelines use additional signals:
- `EVAL_HIRE_COMPLETE`, `EVAL_STRESS_COMPLETE`, `EVAL_DAY2_COMPLETE` — repo-eval evaluators
- `AUDIT_COMPLETE`, `DOC_AUDIT_COMPLETE` — health/doc auditors

## Feedback Mechanics

All review feedback flows through `docs/plans/<plan_id>/feedback.md`. Plan documents are **never mutated** by reviewers.

### Rhetorical Questions

Reviewers don't say "fix line 45." They ask:

```markdown
> **Consider:** The test expects a 401 status code. Are you returning
> the correct HTTP status in your error handling?
>
> **Think about:** What happens when the token is invalid? Is the error
> properly caught?
```

This produces better fixes. When the Implementer is guided to *think about* the problem, it finds root causes and related issues rather than making mechanical edits.

### Feedback Lifecycle

1. Reviewer appends feedback under "Active Feedback" with `Status: OPEN`
2. Generator addresses the item
3. Generator moves it to "Resolved Feedback" with a resolution note
4. Reviewer verifies on next iteration

## Roles

### Shared Across All Flows

**Planner** (Generator) — Creates phased implementation plans. Writes for a zero-context engineer: someone skilled but with no codebase knowledge who will follow instructions precisely and will not infer missing details.

**Plan Reviewer** (Discriminator) — Validates plans through adversarial checks:
- **Deadlock Search:** Circular task dependencies?
- **False Positive Verification:** Could checklists pass with wrong implementation?
- **Ambiguity Search:** Could instructions be interpreted two valid ways?
- **Legacy Code Reality Check:** Does "Modify file.js" reference a file that exists?

### Feature Flow

**Implementer** (Generator) — Executes plans using TDD (red/green/refactor) with atomic conventional commits.

**Code Reviewer** (Discriminator) — Verifies implementation against spec and Phase-0 conventions. Checks for placeholder tests, security issues, error path coverage.

**Final Reviewer** (Discriminator) — Holistic integration review across all phases. Produces a Production Readiness Dashboard with GO/NO-GO verdict.

### Repo-Eval Flow

Three evaluator lenses run in parallel, simulating a hiring panel:

**The Pragmatist** (eval-hire.md) — "Would I trust this person to ship features?" Scores: Problem-Solution Fit, Architecture, Code Quality, Creativity.

**The Oncall Engineer** (eval-stress.md) — "Will this code page me at 3am?" Scores: Pragmatism, Defensiveness, Performance, Type Rigor.

**The Team Lead** (eval-day2.md) — "Can I onboard a junior into this codebase?" Scores: Test Value, Reproducibility, Git Hygiene, Onboarding.

After evaluation, a **Calibration** step normalizes scores across lenses (divergences ≥3 points on overlapping concerns are flagged as signal). Users can set per-pillar thresholds or exclude pillars via `pillar_overrides`.

The evaluators run once, at intake. After remediation, a verification reviewer checks the eval's remediation targets; the evaluators do not re-run.

### Repo-Health Flow

**Health Auditor** (Discriminator) — Pure assessment across 4 vectors: architectural, structural, operational, hygiene. Produces a prioritized ledger with `file:line` locations. Does NOT prescribe fixes.

**Hygienist** (Generator) — Subtractive. Removes dead code, extracts secrets to env vars, removes unused dependencies. Makes the codebase smaller.

**Fortifier** (Generator) — Additive. Adds linting, pre-commit hooks, type strictness, CI gates. Locks in the clean state.

**Health Reviewer** (Discriminator) — Reviews both hygienist and fortifier work using separate checklists selected by `[HYGIENIST]`/`[FORTIFIER]` phase tags.

### Doc-Health Flow

**Doc Auditor** (Discriminator) — 6-phase audit: discovery, comparison (drift/gaps/stale), code examples, link integrity, config/environment, structure.

**Doc Engineer** (Generator) — Fixes drifted docs, deletes stale docs, creates stubs, adds prevention tooling (linting, link checking, auto-gen).

**Doc Reviewer** (Discriminator) — Verifies doc accuracy against source code and that prevention tools actually work.

## Phase-0: The Source of Truth

Every plan starts with Phase-0, which defines immutable rules inherited by all subsequent phases:
- Tech stack and libraries
- Architecture decisions (ADRs)
- Testing strategy (mocking approach for CI)
- Deployment strategy
- Shared patterns and conventions
- Commit message format

Every reviewer checks against Phase-0. This prevents drift across phases (e.g., one phase using Jest while another sets up Vitest).

## Token Budget

Phases target `CLAUDE_FORGE_PHASE_TARGET_TOKENS` (default 150k) for large features. For smaller scopes (remediation, cleanup), phases can be much smaller: the planner sizes to the work, not the budget. The Plan Reviewer flags any phase above `CLAUDE_FORGE_PHASE_MAX_TOKENS` (default 250k) as a context-pressure risk.

## Combined Audits

The `/forge:audit` skill runs multiple audits and produces all intake docs in a single directory. Auditor agents (up to 5) run in parallel since they're read-only. One pipeline run then detects the multiple intake docs and creates ONE unified plan with phases tagged by implementer type.

This is a merged-plan model, not sequential independent flows. The planner reads all audit findings together and creates phases ordered by work type:

1. `[HYGIENIST]` phases first — subtractive cleanup improves subsequent scores
2. `[IMPLEMENTER]` phases next — structural/code fixes on clean code
3. `[FORTIFIER]` phases next — lock in the clean state with guardrails
4. `[DOC-ENGINEER]` phases last — docs reflect the final code state

Each phase tag routes to the correct implementer/reviewer pair. A single verification agent checks the original findings at the end; if three or more remain unverified, the run re-plans them once (at most two verification cycles) before reporting.

## Exit Gates

Each pipeline type has a different completion criteria:

| Pipeline | Exit Gate | Rationale |
|----------|-----------|-----------|
| Feature | Final Reviewer GO/NO-GO | Holistic integration review (only flow with Final Reviewer) |
| Repo-Eval | Verification of remediation targets | One reviewer agent checks specific file:line findings |
| Repo-Health | Verification of CRITICAL/HIGH findings | One reviewer agent checks specific file:line findings; MEDIUM/LOW acceptable to carry |
| Doc-Health | Verification of DRIFT/STALE/BROKEN findings | One reviewer agent checks specific doc:code pairs |

Evaluator and auditor agents run exactly once (during intake). The verification stage uses the existing code reviewer with a targeted prompt — one agent verifying specific findings instead of 3-5 agents re-scanning the entire codebase. The verifier records `VERIFIED` or `UNVERIFIED` under `## Verification` in `feedback.md`.

## State Recovery

Both runners resume from the plan directory. Re-running with the same plan id:

1. Reads which plan files exist, and `feedback.md` for open review items and recorded approvals
2. Checks `git log` for implementation commits per phase
3. Picks the re-entry point: planning, plan review, a phase's implementer (open feedback), a phase's reviewer (implemented or fixed, not yet approved), or the final gate
4. Reports the detected state before continuing

Gates record approvals under `## Approvals` in `feedback.md` (`PLAN_APPROVED`, `PHASE_APPROVED — Phase N`, `GO`), and the verifier records its result under `## Verification`. A phase is skipped only when its approval is recorded; implementation commits alone are not enough. Within a session, a stopped `/forge:run` can also be relaunched from `/workflows`, which replays completed agents from cache.

## NO-GO Rollback

When the feature pipeline's final reviewer issues NO-GO, it records the issues as `FINAL_REVIEW` feedback, categorized:
- **Plan-level issues** → re-enter at Planner with revision instructions
- **Implementation-level issues** → re-enter at the affected phase's Implementer
- **Mixed** → plan-level first, then implementation

Neither runner retries on its own. `/forge:pipeline` routes the rework when you re-run it; `/forge:run` stops at a recorded NO-GO (or UNVERIFIED) until you run `/forge:run <plan-id> rework`, which re-plans from the recorded issues, adds phases for the implementation work, and runs the remaining gates.

## Plan Versioning

Plans use `YYYY-MM-DD-feature-slug` naming:

```
docs/plans/
├── 2026-03-01-user-auth/
├── 2026-03-12-notifications/
└── 2026-03-14-eval-billing-api/
```

Decoupled from release versions. Plans are audit artifacts committed to git — a record of what was designed, what feedback was given, and how it was resolved.

## Skill Run Manifest

Every skill logs a run entry to `.claude/skill-runs.json` in the target repo on completion. This provides a persistent record of when each skill was invoked, what it produced, and which plan directory it created. The file is a JSON array; if it does not exist, the skill creates it.

Each entry varies by skill type:

```json
{"skill": "brainstorm", "date": "2026-03-12", "plan": "2026-03-12-payment-webhooks"}
{"skill": "audit", "date": "2026-03-15", "plan": "2026-03-15-audit-slug", "audits": ["health", "eval", "docs"]}
{"skill": "repo-eval", "date": "2026-03-15", "plan": "2026-03-15-eval-slug"}
{"skill": "pipeline", "date": "2026-03-15", "plan": "2026-03-15-eval-slug", "type": "repo-eval", "verdict": "VERIFIED"}
```

The `pipeline` entry includes the detected pipeline type and final verdict. `/forge:run` does not write this file (see *Two Runners*); its record is the plan directory. The `audit` entry records which audit types were selected. If the file is malformed, the skill overwrites it with a fresh array containing only the new entry.

This log survives OS wipes (it lives in the repo, not a local config directory) and lets users track skill usage across projects over time.

## Safety Rails

- Max 3 iterations per adversarial loop; a loop that doesn't converge stops the run (`MAX_ITERATIONS`) with its open items in `feedback.md`
- NO-GO stops the pipeline — no automatic retry
- Reviewers cannot modify source code — only feedback.md (enforced by each reviewer subagent's frontmatter `tools`)
- Plan documents are immutable once created (only Planner revises)
- Implementer stops and asks rather than guessing
- Roles are native subagents spawned by `subagent_type`; if a needed type is unavailable the orchestrator stops and reports rather than improvising a prompt
- Write ownership enforced (orchestrator writes eval/audit docs; assessor subagents are read-only and cannot write them)

## Defense-in-Depth Tracing

A multi-agent pipeline has no perimeter — the attack surface is internal. Untrusted data (a comment in the codebase under review, an intake doc, a tool result) flows between agents, and a single injection can fan out. The classic failure mode is silent: the swarm does exactly what it was built to do, nothing errors, and the trace looks ordinary.

Forge models this as five **defense points**, the places where an adversary acts, and turns the tracing hook into a passive monitor for each. The hook's leverage is that it reads every subagent's **role from the `agent_type` Claude Code stamps on its hook events and its actions from its own transcript** — which attacker-controlled text cannot forge — so it can attest provenance and re-derive consensus *out of band*, exactly the guarantees the in-band channel (DP3) and the model-aggregator (DP5) can't give themselves.

| DP | Forge surface | Detection (span) | Enforcement already in place |
|----|---------------|------------------|------------------------------|
| DP1 Input boundary | Source files / intake docs an agent reads | `security:dp1.*` — injected instructions or a standalone gate token in read input | — (first-party threat model) |
| DP2 Fan-out | Parallel read-only intake (3 evaluators / ≤5 auditors) on the shared model | `security:dp2.shared_model_fanout` (precondition) | Assessors are read-only (frontmatter `tools`) |
| DP3 Inter-agent channel | Gate signals in agent output + `feedback.md` | `security:dp3.signal_forgery` — a generator/assessor casting a reviewer's ballot | Reviewers can't mutate source; signal authorization is role-checked |
| DP4 Tool boundary | Reviewer trusts Bash test/build output | `security:dp4.approved_without_tests` / `suspicious_command` | No agent gets `Agent` (no nesting) |
| DP5 Aggregation | Orchestrator reads agent output and routes | `security:dp5.aggregator_addressed_instruction` / `decision_starvation` | Orchestrator rules: never skip a reviewer, never self-answer |

Findings are emitted as red (`StatusCode.ERROR`) spans and summarized on `session_complete` as `forge.security.*` attributes — a per-run, ASR-style surface in Jaeger. The layer is detection only (it never blocks), opt-in via `CLAUDE_FORGE_TRACE_SECURITY` (on by default with tracing), and is the natural foundation for an adversarial test tier that asserts on these signals. See the README *Security tracing* section for the span/knob reference.

## Prerequisites

`/forge:run` needs [dynamic workflows](https://code.claude.com/docs/en/workflows), available on paid plans (on Pro, turn on *Dynamic workflows* in `/config`).

The skills (`brainstorm`, the audit skills, and `pipeline`) use the `Agent` tool to spawn the native subagents in `agents/` and `SendMessage` to continue them across review iterations. Those tools are gated behind an experimental flag:

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

Set it in the environment before launching Claude Code. Without it, the skills' agent spawning and message routing fail. The subagent *definitions* are always available (they are auto-discovered from `agents/`).

## Trade-offs

- **Token cost:** Multiple agents reviewing each other's work can triple total token usage
- **Time:** A feature that takes one agent 10 minutes may take the pipeline 30-45 minutes with review loops
- **Orchestrator context:** Under `/forge:pipeline`, long runs accumulate agent reports in the session's context; `/forge:run` keeps them in script variables
- **Re-reading:** Under `/forge:run`, each iteration's agent starts fresh and re-reads the plan files
- **No nesting:** No role gets the `Agent` tool, so all routing stays with the orchestrator

Worth it for features where correctness matters: auth, payments, data integrity, infrastructure. For a quick script, single-pass is fine.
