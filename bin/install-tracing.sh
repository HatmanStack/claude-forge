#!/usr/bin/env bash
#
# Claude Forge — opt-in OpenTelemetry tracing installer.
#
# Installs hooks/trace_subagents.py into a stable user-local path, creates a
# dedicated Python venv with the required opentelemetry packages, and merges
# hook entries into ./.claude/settings.local.json so /pipeline runs emit
# spans to your local OTLP endpoint (Jaeger by default).
#
# Re-runnable. Won't clobber existing keys in settings.local.json.
#
# Usage:
#   bash bin/install-tracing.sh                 # install + write settings to ./.claude/
#   bash bin/install-tracing.sh --no-settings   # install only; print settings snippet
#   bash bin/install-tracing.sh --uninstall     # remove the venv + installed hook
#
set -euo pipefail

# ---------------- paths + constants ----------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_HOOK="$REPO_DIR/hooks/trace_subagents.py"

INSTALL_ROOT="$HOME/.local/share/claude-forge"
VENV="$INSTALL_ROOT/venv"
DEST_HOOK="$INSTALL_ROOT/trace_subagents.py"
VENV_PY="$VENV/bin/python"

PROJECT_DIR="$(pwd)"
SETTINGS_DIR="$PROJECT_DIR/.claude"
SETTINGS_FILE="$SETTINGS_DIR/settings.local.json"

OTEL_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4317}"
OTEL_HOST="$(echo "$OTEL_ENDPOINT" | sed -E 's#^[a-z]+://([^:/]+).*#\1#')"
OTEL_PORT="$(echo "$OTEL_ENDPOINT" | sed -E 's#.*:([0-9]+).*#\1#')"

OTEL_PKGS=(opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc)

# ---------------- helpers ----------------
say()  { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------- uninstall ----------------
if [[ "${1:-}" == "--uninstall" ]]; then
  say "Removing $INSTALL_ROOT"
  rm -rf "$INSTALL_ROOT"
  ok "Uninstalled. Settings.local.json was not touched — edit by hand if needed."
  exit 0
fi

WRITE_SETTINGS=1
[[ "${1:-}" == "--no-settings" ]] && WRITE_SETTINGS=0

# ---------------- 1. verify source hook ----------------
[[ -f "$SRC_HOOK" ]] || die "Hook not found at $SRC_HOOK — is this script inside the claude-forge repo?"

# ---------------- 2. pick installer ----------------
mkdir -p "$INSTALL_ROOT"
if command -v uv >/dev/null 2>&1; then
  INSTALLER="uv"
  say "Using uv ($(uv --version 2>&1 | head -1))"
elif command -v python3 >/dev/null 2>&1; then
  INSTALLER="pip"
  say "Using system python3 ($(python3 --version 2>&1))"
else
  die "Neither uv nor python3 found. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# ---------------- 3. create venv ----------------
if [[ -x "$VENV_PY" ]]; then
  ok "Reusing existing venv at $VENV"
else
  say "Creating venv at $VENV"
  if [[ "$INSTALLER" == "uv" ]]; then
    uv venv "$VENV" >/dev/null
  else
    python3 -m venv "$VENV"
  fi
  ok "Venv created"
fi

# ---------------- 4. install/upgrade deps ----------------
say "Installing opentelemetry packages"
if [[ "$INSTALLER" == "uv" ]]; then
  uv pip install --python "$VENV_PY" --upgrade "${OTEL_PKGS[@]}" >/dev/null
else
  "$VENV/bin/pip" install --quiet --upgrade "${OTEL_PKGS[@]}"
fi
"$VENV_PY" -c "from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter" \
  || die "opentelemetry import still fails after install"
ok "opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc"

# ---------------- 5. copy hook to stable path ----------------
cp "$SRC_HOOK" "$DEST_HOOK"
chmod +x "$DEST_HOOK"
ok "Hook installed: $DEST_HOOK"

HOOK_CMD="$(printf '%q %q' "$VENV_PY" "$DEST_HOOK")"

# ---------------- 6. self-test (without requiring Jaeger) ----------------
say "Running self-test"
SMOKE_LOG="$(mktemp)"
SMOKE_SID="install-smoke-$$"
SMOKE_TRACING_DIR="${TMPDIR:-/tmp}/claude-forge-tracing/$SMOKE_SID"
trap 'rm -rf "$SMOKE_TRACING_DIR" "$SMOKE_LOG"' EXIT

run_smoke() {
  echo "$1" | CLAUDE_FORGE_TRACING=1 OTEL_EXPORTER_OTLP_ENDPOINT="$OTEL_ENDPOINT" \
    "$VENV_PY" "$DEST_HOOK" >>"$SMOKE_LOG" 2>&1
}
run_smoke "{\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"$SMOKE_SID\",\"prompt\":\"install smoke\",\"cwd\":\"$PROJECT_DIR\"}"
run_smoke "{\"hook_event_name\":\"Stop\",\"session_id\":\"$SMOKE_SID\"}"

if [[ -s "$SMOKE_LOG" ]]; then
  warn "Self-test produced output (review $SMOKE_LOG):"
  cat "$SMOKE_LOG" | sed 's/^/    /'
fi
ok "Hook executed without error"

# Optional: probe the OTLP endpoint
if (exec 3<>/dev/tcp/"$OTEL_HOST"/"$OTEL_PORT") 2>/dev/null; then
  exec 3<&-
  ok "OTLP endpoint reachable: $OTEL_ENDPOINT"
else
  warn "OTLP endpoint NOT reachable: $OTEL_ENDPOINT"
  warn "  Start Jaeger before tracing will work, e.g.:"
  warn "    docker run -d --name jaeger --restart unless-stopped \\"
  warn "      -p 16686:16686 -p 4317:4317 -p 4318:4318 jaegertracing/jaeger:latest"
fi

# ---------------- 7. write/merge settings ----------------
SETTINGS_SNIPPET=$(cat <<JSON
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "$HOOK_CMD" } ] }
    ],
    "PreToolUse": [
      { "matcher": ".*", "hooks": [ { "type": "command", "command": "$HOOK_CMD" } ] }
    ],
    "PostToolUse": [
      { "matcher": ".*", "hooks": [ { "type": "command", "command": "$HOOK_CMD" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "$HOOK_CMD" } ] }
    ]
  }
}
JSON
)

if [[ "$WRITE_SETTINGS" == "0" ]]; then
  echo
  say "--no-settings: skipping settings.local.json. Add this to your project:"
  echo "$SETTINGS_SNIPPET"
else
  say "Merging hooks into $SETTINGS_FILE"
  mkdir -p "$SETTINGS_DIR"
  "$VENV_PY" - "$SETTINGS_FILE" "$HOOK_CMD" <<'PY'
import json, os, sys
path, cmd = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        print(f"  warning: existing {path} is not valid JSON; backing up to .bak and rewriting")
        os.rename(path, path + ".bak")
        data = {}
hooks = data.setdefault("hooks", {})
def upsert(event, entry):
    items = hooks.setdefault(event, [])
    # Remove any prior claude-forge tracing entry pointing at the same script.
    items[:] = [
        it for it in items
        if not any(
            isinstance(h, dict) and "trace_subagents.py" in (h.get("command") or "")
            for h in (it.get("hooks") or [])
        )
    ]
    items.append(entry)
plain = {"type": "command", "command": cmd}
upsert("UserPromptSubmit", {"hooks": [plain]})
upsert("PreToolUse",       {"matcher": ".*", "hooks": [plain]})
upsert("PostToolUse",      {"matcher": ".*", "hooks": [plain]})
upsert("Stop",             {"hooks": [plain]})
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  ok "Settings updated. Existing keys preserved."
fi

# ---------------- 8. final summary ----------------
echo
ok "Tracing installed."
echo
echo "  Hook:     $DEST_HOOK"
echo "  Python:   $VENV_PY"
[[ "$WRITE_SETTINGS" == "1" ]] && echo "  Settings: $SETTINGS_FILE"
echo "  Endpoint: $OTEL_ENDPOINT"
echo
echo "Final step — opt in by setting this in your shell init (e.g. ~/.bashrc):"
echo
echo "    export CLAUDE_FORGE_TRACING=1"
echo
if [[ -z "${CLAUDE_FORGE_TRACING:-}" ]]; then
  warn "CLAUDE_FORGE_TRACING is NOT set in this shell — tracing will be a no-op until you set it."
else
  ok "CLAUDE_FORGE_TRACING=$CLAUDE_FORGE_TRACING already set."
fi
echo "Then restart Claude Code from that shell. View traces at http://localhost:16686"
