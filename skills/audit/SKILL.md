---
name: audit
description: Run one or more codebase audits (evaluation, health, documentation) with parallel agent execution, producing intake docs for a single /pipeline run.
allowed-tools: Agent, Read, Write, Glob, Grep, Bash
---

# Audit

You coordinate one or more codebase audits. Ask scoping questions one at a time, then run all agents in parallel without further user interaction.

## Input

`$ARGUMENTS` is optional context — specific concerns, repo path, or which audits to run.

## Process

### Step 1: Select Audits

Ask the user which audits to run. **This is always the first and only question in the first message.**

```text
Which audits should I run?

A) All three (health + eval + docs)
B) Code evaluation — 12-pillar scoring across 3 lenses
C) Technical debt — audit across 4 vectors
D) Documentation — drift detection across 6 phases
```

If `$ARGUMENTS` already specifies which audits (e.g., "/audit all"), skip this question and proceed to Step 2.

Wait for the user's answer before continuing.

### Step 2: Ask Follow-Up Questions One at a Time

Based on which audits were selected, ask the relevant scoping questions **one per message**. Wait for each answer before asking the next.

**Start with the universal question, then ask audit-specific questions.**

**Universal (always ask first):**

1. Known pain points — gives all auditors a starting hypothesis instead of scanning cold.

```text
Are there parts of the codebase you already know are problematic?
Things that keep breaking, areas you dread touching, modules that slow down every PR.

A) Yes (tell me which areas and what's wrong)
B) No — scan everything with fresh eyes
```

**If eval selected (B or A):**

The code evaluation runs 3 evaluator agents in parallel, each scoring 4 pillars (12 total). The scores calibrate to the role level you select.

1. Role level — sets the scoring bar. A "Senior" evaluation expects production-hardened patterns; a "Junior" evaluation focuses on fundamentals.

```text
What role level should I evaluate this codebase against?

A) Junior Developer — fundamentals: readability, basic error handling, test presence
B) Mid-Level Developer — patterns: separation of concerns, consistent conventions, test coverage
C) Senior Developer — production: defensive coding, observability, performance awareness, type rigor
D) Staff+ / Principal — systems: architectural coherence, scalability, operational excellence
```

1. Focus areas — narrows what the evaluators pay extra attention to. They still score all 12 pillars regardless.

```text
Any specific concerns the evaluators should weight more heavily?

A) Performance — hot paths, algorithmic complexity, resource management
B) Security — input validation, auth patterns, secrets handling
C) Testing — coverage quality, test architecture, edge cases
D) Architecture — separation of concerns, modularity, coupling
E) Multiple (tell me which)
F) None — balanced evaluation across all pillars
```

1. Scope and exclusions — what to evaluate and what to skip.

```text
What should the evaluators look at?

A) Full repo, standard exclusions (vendor, generated, node_modules, __pycache__)
B) Full repo, no exclusions
C) Specific directories only (tell me which to include or exclude)
```

1. Pillar overrides — by default, the pipeline remediates until all 12 pillars hit 9/10. Some pillars (like Creativity) may not be improvable through code changes. Override lets you set a lower threshold or exclude a pillar from the remediation gate entirely.

The 12 pillars are:
- **Hire lens:** Problem-Solution Fit, Architecture, Code Quality, Creativity
- **Stress lens:** Pragmatism, Defensiveness, Performance, Type Rigor
- **Day 2 lens:** Test Value, Reproducibility, Git Hygiene, Onboarding

```text
Any pillars to accept below the default 9/10 threshold?

A) None — require 9/10 on all 12 pillars
B) Specific overrides (tell me which pillars and target scores, e.g., "Creativity: 7, Git Hygiene: accept")
```

**If health selected (C or A):**

The health audit scans for technical debt across 4 vectors: architectural, structural, operational, and code hygiene. Findings are prioritized by severity (CRITICAL > HIGH > MEDIUM > LOW). The pipeline remediates until all CRITICAL and HIGH findings are resolved.

1. Goal — determines which debt vectors the auditor emphasizes.

```text
What's the primary goal for this audit?

A) General health check — scan all 4 vectors equally
B) Production hardening — emphasize operational debt (error handling, timeouts, resource leaks, observability)
C) Onboarding prep — emphasize structural and hygiene debt (naming, dead code, documentation, test coverage)
D) Pre-release cleanup — focus on CRITICAL/HIGH items only, skip MEDIUM/LOW
```

1. Deployment target — changes what "operational debt" means. A Lambda function has different concerns than a long-running container.

```text
What's the deployment target?

A) Serverless (Lambda, Cloud Functions) — cold starts, execution limits, stateless constraints
B) Containers (ECS, Kubernetes, Docker) — resource management, health checks, graceful shutdown
C) Static hosting / SPA — build pipeline, CDN, client-side concerns
D) Monolith / traditional server — process management, connection pooling, memory leaks
E) Multiple (tell me which)
F) Not deployed yet / unsure
```

1. Scope and constraints — what to audit and what's off-limits, in one question.

```text
What should the health auditor cover, and is anything off-limits?

A) Full repo, no constraints
B) Full repo, but skip specific areas (tell me which — e.g., "don't touch the legacy auth module")
C) Specific directories only (tell me which)
```

1. Existing tooling — helps the fortifier (hardening phase) know what guardrails already exist so it doesn't duplicate work.

```text
What development tooling is already in place?

A) Full setup — linters, CI pipeline, pre-commit hooks, type checking
B) Partial (tell me what you have — e.g., "ESLint but no CI")
C) None — no linting, CI, or hooks configured
```

**If docs selected (D or A):**

The doc audit runs 6 detection phases: discovery, comparison (drift/gaps/stale), code examples, link integrity, config/environment, and structure. It compares documentation claims against actual code behavior.

1. Scope and constraints — what docs to audit and what's off-limits.

```text
What documentation should I audit, and is anything off-limits?

A) All docs, no constraints
B) All docs, but skip specific files (tell me which)
C) Specific directories only (tell me which)
D) README and API docs only
```

1. Language stack — determines which auto-generation tools are available (typedoc for TS, sphinx for Python, swagger for REST APIs).

```text
What's the primary language stack?

A) JS/TS — typedoc, swagger-jsdoc available
B) Python — sphinx, mkdocstrings available
C) Both
```

1. Prevention tooling — what automated checks to add so documentation drift becomes a CI failure instead of a periodic cleanup.

```text
What drift prevention tooling should I add after fixing the docs?

A) Markdown linting (markdownlint) + link checking (lychee) — catches formatting issues and broken links on every PR
B) Auto-generated API docs (typedoc/sphinx) — single source of truth lives in code, not prose
C) Both A and B
D) None — just fix the existing docs, no new tooling
```

### Step 3: Generate Plan Identifier

After all questions are answered, generate the directory name: `YYYY-MM-DD-audit-slug`

- Date: today's date
- Slug: short name for the repo (e.g., `audit-ragstack`, `audit-my-app`)
- Location: `docs/plans/YYYY-MM-DD-audit-slug/`

Create the directory.

### Step 4: Roles Are Native Subagents

Each evaluator and auditor is a native plugin subagent (under `agents/`) spawned by its `subagent_type`. There are no role prompt files to read — the subagent definition supplies the prompt, and you pass only the `<task>`.

### Step 5: Spawn All Agents in Parallel

All auditor/evaluator agents are read-only — they explore the codebase but don't modify it. Spawn all selected agents in a single parallel batch (up to 5 agents for "all"):

```text
+-------------------------------------------------------------------+
|                    PARALLEL AGENT SPAWN                            |
+-------------------------------------------------------------------+
|                                                                   |
|  health auditor ─┐                                                |
|  eval hire ──────┤                                                |
|  eval stress ────┤  all agents run simultaneously                 |
|  eval day2 ──────┤                                                |
|  doc auditor ────┘                                                |
|                  ↓                                                |
|  orchestrator collects all responses, writes intake docs          |
|                                                                   |
+-------------------------------------------------------------------+
```

**Agent 1: Health Auditor** (if health selected)

Spawn an **Agent** with `subagent_type="forge:health-auditor"`, `name="health-auditor"`:

```text
<task>
Audit the codebase in the current working directory.
Goal: [from Step 2]
Scope: [from Step 2]
Existing tooling: [from Step 2]
Constraints: [from Step 2]
</task>
```

**Agent 2: Eval — The Pragmatist** (if eval selected)

Spawn an **Agent** with `subagent_type="forge:eval-hire"`, `name="eval-hire"`:

```text
<task>
Evaluate the codebase in the current working directory.
Role level: [from Step 2]
Focus areas: [from Step 2]
Exclusions: [from Step 2]
</task>
```

**Agent 3: Eval — The Oncall Engineer** (if eval selected)

Spawn an **Agent** with `subagent_type="forge:eval-stress"`, `name="eval-stress"`:

```text
<task>
Evaluate the codebase in the current working directory.
Role level: [from Step 2]
Focus areas: [from Step 2]
Exclusions: [from Step 2]
</task>
```

**Agent 4: Eval — The Team Lead** (if eval selected)

Spawn an **Agent** with `subagent_type="forge:eval-day2"`, `name="eval-day2"`:

```text
<task>
Evaluate the codebase in the current working directory.
Role level: [from Step 2]
Focus areas: [from Step 2]
Exclusions: [from Step 2]
</task>
```

**Agent 5: Doc Auditor** (if docs selected)

Spawn an **Agent** with `subagent_type="forge:doc-auditor"`, `name="doc-auditor"`:

```text
<task>
Audit documentation in the current working directory against codebase reality.
Doc scope: [from Step 2]
Constraints: [from Step 2]
</task>
```

### Step 6: Validate and Write Intake Docs

After all agents complete, verify each agent's output contains its completion signal:
- Health auditor: check for `AUDIT_COMPLETE`
- Eval hire: check for `EVAL_HIRE_COMPLETE`
- Eval stress: check for `EVAL_STRESS_COMPLETE`
- Eval day2: check for `EVAL_DAY2_COMPLETE`
- Doc auditor: check for `DOC_AUDIT_COMPLETE`

If any signal is missing, the agent may have been truncated. Report the incomplete agent to the user and do NOT write that intake doc with partial data. Other intake docs with valid signals can still be written.

For agents with valid signals, write the intake docs:

- **Health:** Write `docs/plans/YYYY-MM-DD-audit-slug/health-audit.md` with `type: repo-health` in frontmatter
- **Eval:** Combine all 3 evaluator outputs into `docs/plans/YYYY-MM-DD-audit-slug/eval.md` with `type: repo-eval` and `pillar_overrides` in frontmatter
- **Docs:** Write `docs/plans/YYYY-MM-DD-audit-slug/doc-audit.md` with `type: doc-health` in frontmatter

See the individual intake skill SKILL.md files (repo-health, repo-eval, doc-health) for the exact output templates.

### Step 7: Log to Manifest

Append an entry to `.claude/skill-runs.json` in the repo root. If the file does not exist, create it with an empty array first. Each entry records when a skill was run so that skill usage can be tracked across repos and OS wipes.

```json
{
  "skill": "audit",
  "date": "YYYY-MM-DD",
  "plan": "YYYY-MM-DD-audit-slug",
  "audits": ["health", "eval", "docs"]
}
```

- `audits`: list which audits were selected (subset of health, eval, docs)
- Read the existing file, parse the JSON array, append the new entry, and write it back
- If the file is malformed, overwrite it with a fresh array containing only the new entry

### Step 8: Handoff

```text
Audit complete: docs/plans/YYYY-MM-DD-audit-slug/

Intake docs produced:
- [health-audit.md — X critical, Y high, Z medium, W low]
- [eval.md — N/12 pillars at target]
- [doc-audit.md — X drift, Y gaps, Z stale, W broken links]

To remediate, run:
/pipeline YYYY-MM-DD-audit-slug

The pipeline will create one unified plan across all audit types.
```

## Rules

- **DO** ask the audit selection question first, alone
- **DO** ask follow-up questions one at a time, waiting for each answer
- **DO NOT** prompt the user again after all questions are answered — run all agents autonomously
- **DO NOT** start remediation — your only output is the intake docs
- **DO NOT** re-run evaluator or auditor agents after writing the intake docs — they run exactly once during this skill. Re-evaluation happens later in `/pipeline` after all remediation is complete.
- **DO** spawn each evaluator/auditor by its subagent_type (e.g. forge:eval-hire); the subagent definition supplies the prompt
- **DO** produce all intake docs in the same plan directory
- **DO** report results after each audit completes
