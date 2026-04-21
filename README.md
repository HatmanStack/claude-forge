<p align="center">
  <img src="text.jpeg" alt="Claude Forge" width="700">
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://docs.anthropic.com/en/docs/claude-code"><img src="https://img.shields.io/badge/Built%20for-Claude%20Code-blueviolet" alt="Built for Claude Code"></a>
  <img src="https://img.shields.io/badge/Architecture-GAN--inspired-orange" alt="Architecture: GAN-inspired">
</p>

<p align="center">
 <a href="https://portfolio.hatstack.fun/read/post/Claude-Forge">Blog Post</a> · <a href="docs/ARCHITECTURE.md">Architecture Deep Dive</a>
</p>

Adversarial multi-agent pipeline for Claude Code. Separate AI agents generate and critique each other's work in adversarial feedback loops, where generators produce artifacts, discriminators validate them, and iteration drives convergence. Each agent runs in its own context window with fresh perspective.

## Install

**Plugin** (marketplace):
```bash
/plugin install forge@claude-forge
```

- install for the project
- restart claude instance

**Standalone** (copy into any project):
```bash
cp -r skills/ /path/to/your-project/.claude/skills/
# Or personal (all projects)
cp -r skills/ ~/.claude/skills/
```

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) v1.0.33+ and a git-initialized project.

### Enable Agent Teams (Required)

Claude Forge relies on the `Agent` and `SendMessage` tools for multi-agent orchestration. These now require an experimental feature flag.

**Set the environment variable before launching Claude Code:**

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

To make it permanent, add that line to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.) and restart your terminal.

Without this flag, skills that spawn or communicate with sub-agents will fail.

## Skills

| Skill | Purpose | Output | Next Step |
|-------|---------|--------|-----------|
| `brainstorm` | Interactive design session, explores codebase, asks scoping questions | `brainstorm.md` | `pipeline` |
| `audit` | Combined audit runner, select any combination of eval, health, docs | Multiple intake docs | `pipeline` |
| `repo-eval` | 3-evaluator panel scoring 12 pillars | `eval.md` | `pipeline` |
| `repo-health` | Technical debt audit across 4 vectors | `health-audit.md` | `pipeline` |
| `doc-health` | Documentation drift detection across 6 phases | `doc-audit.md` | `pipeline` |
| `pipeline` | Automated build/remediation cycle, routes by intake doc type | Committed code | Done |

### Usage

```bash
# Feature development
/brainstorm I want to add webhook support for payment events
/pipeline 2026-03-12-payment-webhooks

# Full audit (health > eval > docs) with one pipeline run
/audit all
/pipeline 2026-03-15-audit-my-app

# Or run individual audits (each creates its own plan directory)
/repo-eval
/pipeline 2026-03-15-eval-my-app
```

Resume any interrupted pipeline by re-running `/pipeline` with the same slug.

## Pipeline Flows

<p align="center">
  <img src="arch.jpeg" alt="Claude Forge" width="700">
</p>

### Feature (`brainstorm.md`)

```
Planner ↔ Plan Reviewer → Implementer ↔ Code Reviewer → Final Reviewer
         (max 3 iter)                   (max 3 iter/phase)    GO/NO-GO
```

### Repo Eval (`eval.md`)

```
3 Evaluators → Planner ↔ Plan Reviewer → Implementer ↔ Reviewer → Verify
(parallel)     (max 3)                   (max 3/phase)             verify findings
```

### Repo Health (`health-audit.md`)

```
Auditor → Planner ↔ Plan Reviewer → Hygienist ↔ Health Reviewer → Fortifier ↔ Health Reviewer → Verify
                                     [cleanup]                      [guardrails]                   verify findings
```

### Doc Health (`doc-audit.md`)

```
Doc Auditor → Planner ↔ Plan Reviewer → Doc Engineer ↔ Doc Reviewer → Verify
                                         [fix + prevent]               verify findings
```

## File Structure

```
claude-forge/
├── .claude-plugin/
│   └── plugin.json                 # Plugin manifest
├── skills/
│   ├── audit/SKILL.md              # Combined audit runner
│   ├── brainstorm/SKILL.md
│   ├── repo-eval/SKILL.md
│   ├── repo-health/SKILL.md
│   ├── doc-health/SKILL.md
│   └── pipeline/
│       ├── SKILL.md                # Orchestrator (routes by intake doc type)
│       ├── pipeline-protocol.md    # Signal protocol spec
│       ├── planner.md              # Shared across all flows
│       ├── plan_reviewer.md        # Shared across all flows
│       ├── implementer.md          # Feature + repo-eval flows
│       ├── reviewer.md             # Feature + repo-eval flows
│       ├── final_reviewer.md       # Feature flow only
│       ├── eval-hire.md            # The Pragmatist
│       ├── eval-stress.md          # The Oncall Engineer
│       ├── eval-day2.md            # The Team Lead
│       ├── health-auditor.md       # Pure assessment, no fix guidance
│       ├── health-hygienist.md     # Subtractive (delete, simplify)
│       ├── health-fortifier.md     # Additive (lint, CI, hooks)
│       ├── health-reviewer.md      # Reviews both hygienist + fortifier
│       ├── doc-auditor.md          # 6-phase drift detection
│       ├── doc-engineer.md         # Fix docs + add prevention
│       ├── doc-reviewer.md         # Reviews doc changes
│       └── flows/
│           ├── audit-flow.md       # Unified plan across multiple audit types
│           ├── repo-eval-flow.md
│           ├── repo-health-flow.md
│           └── doc-health-flow.md
├── docs/ARCHITECTURE.md
├── README.md
├── CHANGELOG.md
└── LICENSE
```

## Tracing (optional)

Claude Forge ships an opt-in OpenTelemetry hook that emits a span per subagent invocation, parented to a per-session root span, so a `/pipeline` run shows up as a single trace in Jaeger.

It is **off by default**. Without the env var (and without `opentelemetry` installed) the hook is a no-op and cannot break a tool call.

To turn it on:

1. Run Jaeger (any backend speaking OTLP/gRPC on `:4317` works):
   ```bash
   docker run -d --name jaeger -p 16686:16686 -p 4317:4317 -p 4318:4318 \
     jaegertracing/all-in-one:latest
   ```
2. Install the Python deps in whatever environment your shell uses:
   ```bash
   pip install opentelemetry-api opentelemetry-sdk \
               opentelemetry-exporter-otlp-proto-grpc
   ```
3. Register the hook for your checkout (this file is gitignored):
   ```bash
   cp .claude/settings.local.json.example .claude/settings.local.json
   ```
4. Export the opt-in flag before launching Claude Code:
   ```bash
   export CLAUDE_FORGE_TRACING=1
   # optional override
   export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
   ```

Open the Jaeger UI at <http://localhost:16686> and pick the `claude-forge` service. Unset `CLAUDE_FORGE_TRACING` (or delete `settings.local.json`) to disable.

## License

MIT — see [LICENSE](LICENSE).
