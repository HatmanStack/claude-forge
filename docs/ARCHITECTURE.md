# Architecture

## The GAN Analogy

Claude Forge applies Generative Adversarial Network principles to software development. In ML, GANs pit a generator against a discriminator — the generator creates content, the discriminator evaluates it, and iterative feedback drives both to improve.

Here, the same principle applies to code:

```
+----------+                    +--------------+
| Generator|  -- artifact -->   | Discriminator|
| (Planner,|  <-- feedback --   | (Plan Review,|
|  Implmtr)|                    |  Code Review)|
+----------+                    +--------------+
```

The key insight: **each agent runs in its own context window**. The Plan Reviewer has never seen the Planner's reasoning process — only the output. The Code Reviewer has never seen the Implementer's struggles — only the code. Fresh context means fresh eyes.

## Signal Protocol

Agents communicate through structured signals routed by the orchestrator:

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

Re-evaluation is **targeted** — only evaluators with pillars below threshold re-run.

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

Phases target ~50k tokens for large features (fits in one agent context window). For smaller scopes (remediation, cleanup), phases can be much smaller — the planner sizes to the work, not the budget. Hard ceiling: 75k tokens per phase to avoid context pressure.

## Combined Audits

The `/audit` skill runs multiple audits and produces all intake docs in a single directory. Auditor agents (up to 5) run in parallel since they're read-only. A single `/pipeline` command then detects the multiple intake docs and creates ONE unified plan with phases tagged by implementer type.

This is a merged-plan model, not sequential independent flows. The planner reads all audit findings together and creates phases ordered by work type:

1. `[HYGIENIST]` phases first — subtractive cleanup improves subsequent scores
2. `[IMPLEMENTER]` phases next — structural/code fixes on clean code
3. `[FORTIFIER]` phases next — lock in the clean state with guardrails
4. `[DOC-ENGINEER]` phases last — docs reflect the final code state

Each phase tag routes to the correct implementer/reviewer pair. A single verification agent verifys the original findings at the end.

## Exit Gates

Each pipeline type has a different completion criteria:

| Pipeline | Exit Gate | Rationale |
|----------|-----------|-----------|
| Feature | Final Reviewer GO/NO-GO | Holistic integration review (only flow with Final Reviewer) |
| Repo-Eval | Verification verify of remediation targets | One reviewer agent checks specific file:line findings |
| Repo-Health | Verification of CRITICAL/HIGH findings | One reviewer agent checks specific file:line findings; MEDIUM/LOW acceptable to carry |
| Doc-Health | Verification of DRIFT/STALE/BROKEN findings | One reviewer agent checks specific doc:code pairs |

Evaluator and auditor agents run exactly once (during intake). The verification stage uses the existing code reviewer with a targeted prompt — one agent verifying specific findings instead of 3-5 agents re-scanning the entire codebase. Max 2 verification cycles before surfacing to user.

## State Recovery

All pipeline types support resumption. When `/pipeline` is re-invoked with the same slug, the orchestrator:

1. Reads `feedback.md` for progress signals (`PLAN_APPROVED`, `PHASE_APPROVED`, etc.)
2. Checks git log for implementation commits per phase
3. Determines the correct re-entry point (which stage, which phase, which iteration)
4. Reports detected state to the user before continuing

A phase is only skip-eligible when `feedback.md` contains a `PHASE_APPROVED` record. Implementation commits alone are not sufficient.

## NO-GO Rollback

When the feature pipeline's final reviewer issues NO-GO, feedback is categorized:
- **Plan-level issues** → re-enter at Planner with revision instructions
- **Implementation-level issues** → re-enter at affected phase Implementer
- **Mixed** → plan-level first, then implementation

The `NO-GO` status in feedback.md is updated to `REWORK_IN_PROGRESS` to distinguish active rework from a fresh run.

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

The `pipeline` entry includes the detected pipeline type and final verdict. The `audit` entry records which audit types were selected. If the file is malformed, the skill overwrites it with a fresh array containing only the new entry.

This log survives OS wipes (it lives in the repo, not a local config directory) and lets users track skill usage across projects over time.

## Safety Rails

- Max 3 iterations per GAN loop before escalating to the user
- NO-GO stops the pipeline — no automatic retry
- Reviewers cannot modify source code — only feedback.md
- Plan documents are immutable once created (only Planner revises)
- Implementer stops and asks rather than guessing
- Role file validation before any agent is spawned
- Write ownership enforced (orchestrator writes eval/audit docs, never agents)

## Trade-offs

- **Token cost:** Multiple agents reviewing each other's work can triple total token usage
- **Time:** A feature that takes one agent 10 minutes may take the pipeline 30-45 minutes with review loops
- **Orchestrator context:** Long pipelines with many phases accumulate agent result summaries
- **No nesting:** Claude Code agents can't spawn sub-agents; the orchestrator manages all routing

Worth it for features where correctness matters: auth, payments, data integrity, infrastructure. For a quick script, single-pass is fine.
