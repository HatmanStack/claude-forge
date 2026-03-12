# Claude Forge

An adversarial development pipeline for Claude Code. Features go through iterative plan-implement-review cycles where separate AI agents check each other's work in a GAN-like architecture — generators (Planner, Implementer) produce artifacts, discriminators (Plan Reviewer, Code Reviewer, Final Reviewer) validate them, and feedback loops drive convergence.

## Architecture

```
You (human)
 |
 |  /brainstorm "build a user auth system"
 v
+------------------------------------------------------------------+
|  BRAINSTORM (interactive, main conversation)                     |
|  Explore codebase -> Ask questions -> Produce design spec        |
+------------------------------------------------------------------+
 |
 |  /pipeline 2026-03-12-user-auth
 v
+------------------------------------------------------------------+
|  PIPELINE ORCHESTRATOR (spawns agents, routes signals)            |
|                                                                  |
|  Stage 1: Planning                                               |
|  +------------+     +--------------+                             |
|  |  Planner   | <-> | Plan Reviewer|  GAN loop (max 3 iters)    |
|  | (generator)|     | (discriminator)                            |
|  +------------+     +--------------+                             |
|        |                                                         |
|  Stage 2: Implementation (per phase)                             |
|  +--------------+     +----------+                               |
|  | Implementer  | <-> | Reviewer |    GAN loop (max 3 iters)    |
|  | (generator)  |     | (discriminator)                          |
|  +--------------+     +----------+                               |
|        |                                                         |
|  Stage 3: Final Review                                           |
|  +----------------+                                              |
|  | Final Reviewer |  GO / NO-GO                                  |
|  +----------------+                                              |
+------------------------------------------------------------------+
```

Each agent runs in its own context window with a fresh perspective. Feedback flows through a shared `feedback.md` file — plan documents are never mutated by reviewers.

## Skills

### `/brainstorm` — Interactive Design Session

Explores your codebase, asks 5-15 clarifying questions (preferring multiple choice), and produces a structured design spec.

**Output:** `docs/plans/YYYY-MM-DD-feature-slug/brainstorm.md`

```
/brainstorm I want to add real-time notifications to the dashboard
```

### `/pipeline` — Automated Build Cycle

Reads the brainstorm doc and runs the full plan-implement-review pipeline. Each role is a separate agent with its own context window.

```
/pipeline 2026-03-12-notifications
```

## Pipeline Roles

| Role | Type | Agent Context | Responsibility |
|------|------|---------------|----------------|
| **Planner** | Generator | Fresh per invocation | Creates phased implementation plans (~50k tokens/phase) |
| **Plan Reviewer** | Discriminator | Fresh per invocation | Validates plan logic, file existence, dependency chains, adversarial checks |
| **Implementer** | Generator | Fresh per phase | Executes plan using TDD, makes atomic commits |
| **Code Reviewer** | Discriminator | Fresh per phase | Verifies implementation against spec and Phase-0 conventions |
| **Final Reviewer** | Discriminator | Fresh (once) | Holistic integration review, production readiness assessment |

## Signal Protocol

Agents communicate through structured signals that the orchestrator routes:

| Signal | From | To | Meaning |
|--------|------|----|---------|
| `PLAN_COMPLETE` | Planner | Plan Reviewer | Plan ready for review |
| `REVISION_REQUIRED` | Plan Reviewer | Planner | Issues found, check feedback.md |
| `PLAN_APPROVED` | Plan Reviewer | Implementer | Plan is sound, start building |
| `IMPLEMENTATION_COMPLETE` | Implementer | Reviewer | Phase code ready for review |
| `CHANGES_REQUESTED` | Reviewer | Implementer | Issues found, check feedback.md |
| `PHASE_APPROVED` | Reviewer | Next phase / Final Reviewer | Phase code is solid |
| `GO` | Final Reviewer | Done | Production ready |
| `NO-GO` | Final Reviewer | Planner / Implementer | Issues categorized and routed back |

## Feedback Channel

All review feedback flows through `docs/plans/<plan_id>/feedback.md`. This keeps plan documents clean and gives both review roles a consistent communication channel.

```markdown
## Active Feedback

### CODE_REVIEW - Iteration 1 - Phase 2, Task 3

> **Consider:** The test expects a 401 status code. Are you returning
> the correct HTTP status in your error handling?

**Status:** OPEN

## Resolved Feedback

### PLAN_REVIEW - Iteration 1 - Phase 1, Task 2

> **Consider:** This says "Modify src/utils/date.js" but the file
> doesn't exist. Should this be "Create"?

**Status:** RESOLVED
**Resolution:** Changed to "Create" and added prerequisite setup steps
```

Reviewers use rhetorical questions (Consider / Think about / Reflect) to guide the generator's thinking rather than prescribing exact fixes.

## Plan Versioning

Plans use date-based versioning with a feature slug:

```
docs/plans/
├── 2026-03-01-user-auth/
│   ├── brainstorm.md
│   ├── README.md
│   ├── feedback.md
│   ├── Phase-0.md
│   ├── Phase-1.md
│   └── Phase-2.md
├── 2026-03-12-notifications/
│   ├── brainstorm.md
│   └── ...
```

- Decoupled from release versions (no collision with existing tags)
- Self-sorting chronologically
- Greppable by feature name
- Plans are committed as audit artifacts

## Installation

### Per-project (recommended for teams)

Copy the `.claude/skills/` directory into your project:

```bash
cp -r .claude/skills/ /path/to/your-project/.claude/skills/
```

Anyone who clones the project gets the skills automatically.

### Personal (all your projects)

Copy into your personal Claude Code config:

```bash
cp -r .claude/skills/ ~/.claude/skills/
```

Available in every project on your machine.

## File Structure

```
.claude/skills/
├── brainstorm/
│   └── SKILL.md              # Interactive Q&A skill
└── pipeline/
    ├── SKILL.md              # Orchestrator (spawns and routes agents)
    ├── planner.md            # Planning architect role prompt
    ├── plan_reviewer.md      # Plan review role prompt
    ├── implementer.md        # Implementation engineer role prompt
    ├── reviewer.md           # Code review role prompt
    ├── final_reviewer.md     # Final review role prompt
    └── pipeline-protocol.md  # Signal protocol and feedback channel spec
```

## Safety Rails

- **Max 3 iterations** per GAN loop before surfacing to the user
- **NO-GO** from final review stops and asks for human input (no auto-retry)
- **Reviewers cannot modify source code** — only feedback.md
- **Plan documents are immutable** once created (only Planner can revise)
- **Progress reports** between stages keep the user informed
- **Implementer stops and asks** rather than guessing on ambiguous instructions

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- A git-initialized project to work in

## License

MIT
