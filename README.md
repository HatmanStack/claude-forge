<p align="center">
  <img src="text.jpeg" alt="Claude Forge" width="700">
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://docs.anthropic.com/en/docs/claude-code"><img src="https://img.shields.io/badge/Built%20for-Claude%20Code-blueviolet" alt="Built for Claude Code"></a>
  <img src="https://img.shields.io/badge/Architecture-GAN--inspired-orange" alt="Architecture: GAN-inspired">
</p>

<p align="center">
 <a href="https://portfolio.hatstack.fun/read/post/Claude-Forge">Blog Post</a> · <a href="docs/ARCHITECTURE.md">Architecture Deep Dive</a> · <a href="https://portfolio.hatstack.fun/read/post/Jaeger-Tracing-In-Multi-Agent-Systems"> Tracing</a>
</p>

Adversarial multi-agent pipeline for Claude Code. Separate AI agents generate and critique each other's work in adversarial feedback loops, where generators produce artifacts, discriminators validate them, and iteration drives convergence. Each agent runs in its own context window with fresh perspective.

## Install

**Plugin** (marketplace):
```bash
/plugin marketplace add hatmanstack/claude-forge
/plugin install forge@claude-forge
/reload-plugins
```

The first command registers the marketplace (persisted to `~/.claude/plugins/known_marketplaces.json`, so you only do it once). The second opens the install TUI — select a scope and confirm. The third activates the plugin in your current session.

When installed as a plugin, skills are prefixed with `forge:` — e.g. `/forge:pipeline`, `/forge:brainstorm`. The unprefixed forms (`/pipeline`, etc.) shown in the usage examples below apply to the standalone install path.

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
├── bin/
│   └── install-tracing.sh          # Optional Jaeger/OpenTelemetry setup
├── hooks/
│   └── trace_subagents.py          # Tracing hook (installed via bin/install-tracing.sh)
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

Claude Forge ships an opt-in OpenTelemetry hook that emits one span per subagent invocation, parented to a per-session root, so a `/pipeline` run shows up as a single trace in Jaeger with per-subagent token counts and durations.

It is **off by default**. Without `CLAUDE_FORGE_TRACING=1` the hook is a no-op and cannot break a tool call.

### 1. Install Docker

Skip if you already have Docker.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```

> Ubuntu users: replace both `linux/debian` URLs with `linux/ubuntu`.

### 2. Run Jaeger v2

```bash
docker run -d --name jaeger --restart unless-stopped \
  -p 16686:16686 -p 4317:4317 -p 4318:4318 \
  jaegertracing/jaeger:latest
```

That gets you the UI at <http://localhost:16686> and OTLP/gRPC ingestion on `:4317`. Storage is in-memory — traces are lost on container restart, which is fine for local development.

### 3. Install the tracing hook

From a clone of this repo:

```bash
cd your-project   # the project where you'll run /pipeline
bash /path/to/claude-forge/bin/install-tracing.sh
```

If you installed Claude Forge as a plugin (`/plugin install forge@claude-forge`), the script ships *inside* the plugin install directory. Locate and run it the same way:

```bash
cd your-project
bash "$(find ~/.claude -path '*/forge*' -name install-tracing.sh 2>/dev/null | head -1)"
```

The script:
- Creates a dedicated venv at `~/.local/share/claude-forge/venv` (uses [`uv`](https://astral.sh/uv) if installed, otherwise `python3 -m venv`)
- Installs `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` into that venv
- Copies the hook to `~/.local/share/claude-forge/trace_subagents.py`
- Merges hook entries into `./.claude/settings.local.json` (preserves any existing keys)
- Self-tests: runs the hook end-to-end and probes the OTLP endpoint

Flags: `--no-settings` (install only, print snippet), `--uninstall` (remove the venv + hook).

**Tip — alias it.** You'll re-run this command per-project (one-time wiring) and after every claude-forge release (to refresh the shared hook). Add to `~/.bashrc` (or `~/.zshrc`) once:

```bash
forge-trace() {
  local s
  s=$(find ~/.claude -path '*/forge*' -name install-tracing.sh 2>/dev/null | head -1)
  [[ -z "$s" ]] && { echo "forge plugin not installed"; return 1; }
  bash "$s" "$@"
}
```

Then it's just `forge-trace` (or `forge-trace --no-settings`, `forge-trace --uninstall`) from any project.

### 4. Opt in

Add to your shell init (`~/.bashrc`, `~/.zshrc`, etc.) and restart your terminal:

```bash
export CLAUDE_FORGE_TRACING=1
# optional override; defaults to http://localhost:4317
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

Restart Claude Code from that shell, run `/pipeline`, then open <http://localhost:16686> and pick the `claude-forge` service. To disable, unset the env var (or run `bash bin/install-tracing.sh --uninstall`).

### Updating tracing

When a new claude-forge release updates the hook, plugin users need two commands to pull it through:

```
/plugin marketplace update claude-forge    # refreshes ~/.claude/plugins/cache/claude-forge
```
then in a shell, from any project:
```bash
forge-trace                                # if you set up the alias above
# or, without the alias:
bash "$(find ~/.claude -path '*/forge*' -name install-tracing.sh 2>/dev/null | head -1)"
```

The install script re-copies the hook from the refreshed plugin cache to `~/.local/share/claude-forge/trace_subagents.py`. Because every project's `settings.local.json` points to that absolute path, **all projects pick up the new hook automatically** on their next tool call — no per-project re-run and no Claude Code restart needed. If you skip the marketplace-update step, the install script will happily copy the *old* cached hook and your traces won't reflect the release.

### Other tracing knobs

| Variable | Default | Purpose |
|---|---|---|
| `CLAUDE_FORGE_TRACING` | unset | Master on/off — hook is a no-op without this |
| `CLAUDE_FORGE_TRACE_MUTATIONS` | `1` (on) | Trace each subagent's mutational tool calls (`Write`, `Edit`, `MultiEdit`, `Bash`) as child spans. On by default — these show *what* each subagent changed. Set to `0` for pure agent-level traces. |
| `CLAUDE_FORGE_TRACE_INNER` | unset | Also trace *non-mutational* inner tools (Read/Glob/Grep/etc.). Off by default — a `/pipeline` can fire 200+ such calls. |
| `CLAUDE_FORGE_TRACE_TOOL_BLOCKLIST` | `Read,Glob,Grep,TodoWrite,NotebookRead` | When inner tracing is on, comma-separated tools to skip. Empty string disables the blocklist |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP/gRPC endpoint for any backend (Jaeger, Tempo, Honeycomb, etc.) |

## License

MIT — see [LICENSE](LICENSE).
