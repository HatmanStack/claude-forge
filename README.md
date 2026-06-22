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
cp -r agents/ /path/to/your-project/.claude/agents/
# Or personal (all projects)
cp -r skills/ ~/.claude/skills/
cp -r agents/ ~/.claude/agents/
```

Copy **both** `skills/` and `agents/` — the pipeline roles are native Claude Code subagents that live in `agents/`. When installed standalone, the orchestrator addresses them without the `forge:` plugin prefix (e.g. `planner` instead of `forge:planner`).

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
├── agents/                         # Native subagents (the "team") — auto-discovered as forge:<name>
│   ├── planner.md                  # Generator — shared across all flows
│   ├── plan-reviewer.md            # Discriminator — shared across all flows
│   ├── implementer.md              # Generator — feature + repo-eval flows
│   ├── reviewer.md                 # Discriminator — feature + repo-eval + verification
│   ├── final-reviewer.md           # Discriminator — feature flow only
│   ├── eval-hire.md                # The Pragmatist (read-only)
│   ├── eval-stress.md              # The Oncall Engineer (read-only)
│   ├── eval-day2.md                # The Team Lead (read-only)
│   ├── health-auditor.md           # Pure assessment, no fix guidance (read-only)
│   ├── health-hygienist.md         # Generator — subtractive (delete, simplify)
│   ├── health-fortifier.md         # Generator — additive (lint, CI, hooks)
│   ├── health-reviewer.md          # Discriminator — reviews hygienist + fortifier
│   ├── doc-auditor.md              # 6-phase drift detection (read-only)
│   ├── doc-engineer.md             # Generator — fix docs + add prevention
│   └── doc-reviewer.md             # Discriminator — reviews doc changes
├── skills/
│   ├── audit/SKILL.md              # Combined audit runner
│   ├── brainstorm/SKILL.md
│   ├── repo-eval/SKILL.md
│   ├── repo-health/SKILL.md
│   ├── doc-health/SKILL.md
│   └── pipeline/
│       ├── SKILL.md                # Orchestrator (routes by intake doc type)
│       ├── pipeline-protocol.md    # Signal protocol + subagent-type spec
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

Each role is a **native Claude Code subagent**: its prompt is the file body and its tool/model access is declared in YAML frontmatter. Generators get write access (`Read, Write, Edit, Glob, Grep, Bash`); reviewers are restricted to `feedback.md` edits (`Read, Glob, Grep, Bash, Edit`); evaluators and auditors are strictly read-only (`Read, Glob, Grep, Bash`). The orchestrator skills spawn each role by `subagent_type` (e.g. `forge:planner`) and continue iteration loops with `SendMessage` — no role-prompt text is injected.

## Evaluation

Forge is itself an evaluation system, so it ships an evaluation harness for *its own* team — the regression net that keeps agents, tools, and the orchestrator honest as they change. It follows an evaluation pyramid (fast/deterministic at the base, realistic at the top):

- **Tier A — Contracts** (`evaluation/tier_a_contracts/`): deterministic checks of agent frontmatter, per-role tool policy (generators write, reviewers read-only over source, assessors fully read-only, no agent nests), wiring (`forge:<type>` references resolve), and manifest consistency. Runs on every push/PR — pure stdlib + pytest, no LLM.
- **Tier C — Trajectory** (`evaluation/tier_c_trajectory/`): validates the sequence of governance signals against the protocol — signal provenance (no forged approvals), gate order, and no skipped reviews. Synthetic fixtures per-PR; real runs via `evaluation/check_run.py` against the trace hook's `trace-summary.json`.
- **Tier D — Live traces**: the OpenTelemetry → Jaeger hook (below), including the `security:dp{1..5}.*` defense-in-depth spans.

```bash
python -m pip install pytest
python -m pytest evaluation/ -v
```

See [evaluation/README.md](evaluation/README.md) for the full pyramid and the live-run trajectory check. CI runs Tiers A and C on every push and pull request (`.github/workflows/evaluation.yml`).

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
bash "$(find ~/.claude -path '*/forge*/bin/install-tracing.sh' 2>/dev/null | sort -V | tail -1)"
```

> ⚠️ The `sort -V | tail -1` picks the **latest** cached plugin version. Plain `head -1` will silently pick whatever filesystem iteration order returns first — which is *not* always the newest after you've done a few upgrades, and will roll your deployed hook back to a stale snapshot.

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
  # IMPORTANT: sort -V | tail -1 picks the LATEST cached plugin version.
  # Plain `head -1` returns whatever filesystem iteration order produces,
  # which silently picks an old version once you've upgraded — making
  # forge-trace roll your hook back to a stale snapshot.
  s=$(find ~/.claude -path '*/forge*/bin/install-tracing.sh' 2>/dev/null | sort -V | tail -1)
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

> ⚠️ **Three commands, in order.** Plugin updates are layered: the marketplace index, the installed plugin version, and the deployed hook are independent. Skip a step and you'll silently roll back to whatever was installed last.

```
/plugin marketplace update claude-forge    # 1. refresh marketplace metadata (knows which versions exist)
/plugin install forge@claude-forge         # 2. UPGRADE the installed plugin to the latest version
                                           #    (writes ~/.claude/plugins/cache/claude-forge/forge/<NEW_VERSION>/)
```

then in a shell, from any project:

```bash
forge-trace                                # 3. deploy the refreshed hook (copies plugin cache → ~/.local/share/claude-forge/)
# or, without the alias:
bash "$(find ~/.claude -path '*/forge*/bin/install-tracing.sh' 2>/dev/null | sort -V | tail -1)"
```

**Why all three:** `/plugin marketplace update` only refreshes the *index* of available versions — it does not touch any installed plugin. `/plugin install forge@claude-forge` is what actually upgrades your installed plugin to the new version. Without that step, the plugin cache stays at whatever version you originally installed (e.g. `forge/1.3.2/`), and `forge-trace` will dutifully copy *that* old hook on top of any newer one you'd manually deployed — silently rolling back your tracing.

Verify after the three commands:

```bash
ls ~/.claude/plugins/cache/claude-forge/forge/             # should list the new version directory
grep -c MUTATION_TOOLS ~/.local/share/claude-forge/trace_subagents.py   # should be > 0 (proves the deployed hook is current)
```

The deployed hook lives at `~/.local/share/claude-forge/trace_subagents.py`. Every project's `settings.local.json` points to that absolute path, so **all projects pick up the new hook automatically** on their next tool call — no per-project re-run and no Claude Code restart needed.

### Other tracing knobs

| Variable | Default | Purpose |
|---|---|---|
| `CLAUDE_FORGE_TRACING` | unset | Master on/off — hook is a no-op without this |
| `CLAUDE_FORGE_TRACE_MUTATIONS` | `1` (on) | Trace each subagent's mutational tool calls as child spans. On by default — these show *what* each subagent changed. Set to `0` for pure agent-level traces. |
| `CLAUDE_FORGE_TRACE_MUTATION_TOOLS` | `Write,Edit,MultiEdit` | Comma-separated list of tools traced as mutations. `Bash` is **excluded by default** because pipeline runs invoke it hundreds of times (git, npm, tests, ls) and the noise drowns out Write/Edit visibility. Add it back via `CLAUDE_FORGE_TRACE_MUTATION_TOOLS="Write,Edit,MultiEdit,Bash"` if you need Bash spans. |
| `CLAUDE_FORGE_TRACE_INNER` | unset | Also trace *non-mutational* inner tools (Read/Glob/Grep/etc.). Off by default — a `/pipeline` can fire 200+ such calls. |
| `CLAUDE_FORGE_TRACE_TOOL_BLOCKLIST` | `Read,Glob,Grep,TodoWrite,NotebookRead` | When inner tracing is on, comma-separated tools to skip. Empty string disables the blocklist |
| `CLAUDE_FORGE_TRACE_SECURITY` | `1` (on) | Defense-in-depth detection layer (see below). Emits `security:dp{1..5}.*` spans and a per-session summary. Detection only — never blocks a tool call. Set `0` to disable. |
| `CLAUDE_FORGE_SECURITY_INJECTION_EXTRA` | unset | Optional extra regex appended to the DP1 instruction-injection pattern set (for repo-specific markers) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP/gRPC endpoint for any backend (Jaeger, Tempo, Honeycomb, etc.) |
| `CLAUDE_FORGE_PHASE_TARGET_TOKENS` | `150000` | Target token budget per pipeline phase (Stage size). Read by the Planner when sizing phases and by the Plan Reviewer when judging them. Smaller values produce more, smaller phases; larger values produce fewer, larger phases. |
| `CLAUDE_FORGE_PHASE_MAX_TOKENS` | `250000` | Hard ceiling per phase — Planner must not exceed this and Plan Reviewer flags phases above it (context-pressure risk). |

### Security tracing (defense in depth)

A multi-agent pipeline can be subverted without a single error in the logs — a poisoned file comment, a forged approval signal, or a reviewer that rubber-stamps. When `CLAUDE_FORGE_TRACING=1`, Forge adds a **passive detection layer** that makes those events visible in Jaeger. It reads each subagent's role and actions from Claude Code's own transcript metadata — which file/text content can't forge — so it can attest signal provenance and audit consensus out of band. It is **detection only**: it never blocks a tool call or changes pipeline flow, and it's tuned for first-party repos (low false positives). Disable with `CLAUDE_FORGE_TRACE_SECURITY=0`.

Findings surface as `security:dp{1..5}.*` spans (red, status ERROR) plus `forge.security.*` attributes on the `session_complete` span (`findings_total`, per-DP counts, `flagged`, and a `signal_timeline`):

| Span | Defense point | Fires when |
|------|---------------|-----------|
| `security:dp1.injected_instruction` / `…forged_signal_in_input` | Input boundary | An agent read a file containing instruction-like text or a standalone gate token (e.g. an injected `PHASE_APPROVED`) |
| `security:dp2.shared_model_fanout` | Fan-out | ≥2 read-only assessors fanned out over the same input on the shared session model (correlated-compromise precondition) |
| `security:dp3.signal_forgery` | Inter-agent channel | A generator/assessor emitted a gate signal it isn't authorized to cast (forged ballot) |
| `security:dp4.approved_without_tests` / `…suspicious_command` | Tool boundary | A reviewer approved without running tests/build, or any agent ran a check-defeating command (`--no-verify`, `\|\| true`, …) |
| `security:dp5.aggregator_addressed_instruction` / `…decision_starvation` | Aggregation | An agent's output tried to instruct the orchestrator, or a reviewer only ever requested changes (denial of decision) |

Query `security.severity=high` in Jaeger to triage, or alert on `forge.security.flagged=true`. The frontmatter tool lockdown (reviewers can't mutate source; no agent gets `Agent`) is the matching *enforcement* layer — a blocked attempt shows up as a `permission_denied:*` span.

## License

MIT — see [LICENSE](LICENSE).
