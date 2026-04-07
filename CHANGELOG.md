# Changelog

All notable changes to Claude Forge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.2] - 2026-04-06

### Added

- **Dependabot Auto-Merge Workflow** — `.github/workflows/dependabot-auto-merge.yml` merges patch and minor dependency updates automatically after CI passes. Uses `dependabot/fetch-metadata@v2` with an `if:` condition on `update-type` to skip major updates, `gh pr checks --watch --required` to enforce CI gate, and `gh pr merge --auto --squash` for clean history

### Changed

- **Planner Pre-Planning Context** — Planner agent now reads `CLAUDE.md`, `.claude/settings.local.json`, and relevant memory files before writing any plan. Extracted conventions (package manager, runtime, build/test/deploy commands, user preferences) are incorporated into Phase-0.md so the implementer inherits project context. Prevents plans that contradict established stack choices (e.g. using pip when the project uses uv)

## [1.3.1] - 2026-04-01

### Changed

- **Agent Teams Feature Flag** — Documented `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` as a required environment variable. The `Agent` and `SendMessage` tools used for multi-agent orchestration are now gated behind this experimental flag. Added setup instructions to README and a prerequisites section to ARCHITECTURE.md

## [1.3.0] - 2026-03-26

### Added

- **Plugin Format** — Restructured repo as a Claude Code plugin with `.claude-plugin/plugin.json` manifest and `marketplace.json` for marketplace distribution. Install via `/plugin marketplace add hatmanstack/claude-forge`
- **Skill Run Manifest** — Every skill logs a run entry to `.claude/skill-runs.json` on completion, tracking skill name, date, plan directory, and pipeline verdict across projects

### Changed

- **Skills Location** — Moved from `.claude/skills/` to `skills/` at repo root (plugin format requirement). All internal path references updated
- **GAN Framing** — Replaced "applies GAN principles" and "GAN-style loops" with "adapts the adversarial feedback loop from GAN architecture" and "adversarial loops" throughout. Aligns repo language with external content (freeCodeCamp article, blog post)
- **README** — Documents both plugin install (`/plugin install forge@claude-forge`) and standalone install (`cp -r skills/`). Updated file structure diagram

## [1.2.0] - 2026-03-19

### Added

- **Combined Audit Skill** (`/audit`) — Single entry point to run any combination of eval, health, and doc audits. Asks scoping questions one at a time, spawns up to 5 agents in parallel, produces all intake docs in one directory for a single `/pipeline` run
- **Unified Audit Flow** (`flows/audit-flow.md`) — Merged-plan model: one planner reads all intake docs and creates one plan with phases tagged `[HYGIENIST]`, `[IMPLEMENTER]`, `[FORTIFIER]`, `[DOC-ENGINEER]`. Tags route each phase to the correct implementer/reviewer pair
- **Verification Stage** — Lightweight final gate replacing expensive re-evaluation. One reviewer agent verifies specific `file:line` findings from intake docs instead of re-running 3-5 evaluator/auditor agents
- **Signal Validation** — Intake skills now validate completion signals (`EVAL_HIRE_COMPLETE`, `AUDIT_COMPLETE`, `DOC_AUDIT_COMPLETE`) before writing intake docs. Truncated agent output is detected and reported
- **VERIFIED/UNVERIFIED Signals** — New pipeline signals persisted to feedback.md for state recovery across interruptions
- **Per-Pillar Threshold Overrides** — Users can set custom thresholds or exclude specific pillars from the remediation gate
- **Cross-Evaluator Calibration** — Normalizes scores across evaluator lenses before planning; divergences ≥3 points flagged as signal

### Changed

- **Agent Reuse via SendMessage** — Planner, Plan Reviewer, Implementer, and Reviewer agents are spawned once and continued via `SendMessage` for subsequent iterations. Preserves context instead of re-reading codebase from scratch each iteration
- **Strict Agent Spawning Rules** — One agent at a time, no duplicates, no per-phase planners, no background agents, no parallel agents in the pipeline orchestrator
- **Orchestrator Never Runs CI** — Only agents run tests, linters, and builds within their own execution. Orchestrator only spawns agents, reads signals, and routes work. Prevents duplicate CI runs and race conditions
- **Intake Questions** — Added "known pain points" (universal) and "deployment target" (health). Merged scope+constraints into single questions. Dropped redundant "context" question from eval
- **Token Budget** — Changed from hard 50k target to flexible guideline. Planner sizes phases to the work; single-phase plans OK for small scopes
- **Distinct Intake Filenames** — `health-audit.md` and `doc-audit.md` replace shared `audit.md`, eliminating frontmatter-based routing
- **Pipeline Protocol** — Updated signal table with VERIFIED/UNVERIFIED and all intake completion signals. Updated file ownership table with intake docs
- **NO-GO Rollback Path** — Documented re-entry routing: plan-level issues go to Planner, implementation-level to affected Implementer

### Fixed

- Stale re-evaluation/re-audit references removed from all role prompts and flow files
- Flow diagram labels updated from "Re-Audit"/"Re-Evaluate" to "Verify"
- State recovery ordering: VERIFIED checked before PHASE_APPROVED-for-all-phases
- feedback.md creation guaranteed before any stage (orchestrator creates if missing)
- audit-flow pre-flight no longer validates evaluator/auditor files (intake-only)
- doc-health template removed stale `ci_platform` field
- Grep pattern regression fixed: `def\b`/`class\b` word boundaries instead of trailing-space code spans

## [1.1.0] - 2026-03-14

### Added

- **Repo Evaluation Pipeline** (`/repo-eval`) — 3-evaluator hiring panel (The Pragmatist, The Oncall Engineer, The Team Lead) scoring codebases across 12 pillars with GAN-loop remediation until all pillars reach 9/10
- **Repo Health Pipeline** (`/repo-health`) — Technical debt audit across 4 vectors (architectural, structural, operational, hygiene) with Option C topology: auditor (pure assessment) → planner → plan reviewer → hygienist (subtractive cleanup) → health reviewer → fortifier (additive guardrails) → health reviewer → re-audit
- **Doc Health Pipeline** (`/doc-health`) — 6-phase documentation drift detection (discovery, comparison, code examples, link integrity, config/env, structure) with remediation and drift prevention tooling
- **Pipeline Type Routing** — `/pipeline` now detects intake document type (`brainstorm.md`, `eval.md`, `health-audit.md`, `doc-audit.md`) and routes to the appropriate flow file
- **Flow Files** — `flows/repo-eval-flow.md`, `flows/repo-health-flow.md`, `flows/doc-health-flow.md` defining stage sequences, agent spawning, GAN loops, and re-evaluation criteria per pipeline type
- **10 Role Prompts** — `eval-hire.md`, `eval-stress.md`, `eval-day2.md`, `health-auditor.md`, `health-hygienist.md`, `health-fortifier.md`, `health-reviewer.md`, `doc-auditor.md`, `doc-engineer.md`, `doc-reviewer.md`
- **State Recovery** — All flow files include resume detection for interrupted pipelines, matching the main pipeline's Stage 0 pattern

### Changed

- **Pipeline SKILL.md** — Added `Write` to allowed-tools, type detection routing in Pre-Flight, explicit frontmatter parsing instructions for `audit.md`
- **Pipeline State Recovery** — Added Stage 0 for detecting prior progress across all stages (plan approval, phase progress, final review) before re-entering the pipeline

## [1.0.0] - 2026-03-12

### Added

- **Adversarial Development Pipeline** — GAN-style generator/discriminator loops for AI-assisted development
- **Brainstorm Skill** (`/brainstorm`) — Interactive design session producing structured specs grounded in codebase exploration
- **Pipeline Skill** (`/pipeline`) — Automated build cycle with 5 agent roles: Planner, Plan Reviewer, Implementer, Code Reviewer, Final Reviewer
- **Signal Protocol** — Structured communication between agents via `PLAN_COMPLETE`, `REVISION_REQUIRED`, `PLAN_APPROVED`, `IMPLEMENTATION_COMPLETE`, `CHANGES_REQUESTED`, `PHASE_APPROVED`, `GO`, `NO-GO`
- **Immutable Plans with Mutable Feedback** — Plan documents created once, all review feedback flows through `feedback.md` with OPEN/RESOLVED tracking
- **Phase-0 Architecture Blueprint** — Shared conventions document inherited by all implementation phases
- **Zero-Context Engineer Simulation** — Plan Reviewer validates that plans are followable without prior codebase knowledge
- **Adversarial Plan Review** — Deadlock search, false positive verification, ambiguity search, missing context detection
- **Token Budgeting** — Phases sized to ~50k tokens to fit single agent context windows
- **Date-Based Plan Versioning** — `YYYY-MM-DD-feature-slug` naming decoupled from release versions
