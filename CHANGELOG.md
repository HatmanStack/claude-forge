# Changelog

All notable changes to Claude Forge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
