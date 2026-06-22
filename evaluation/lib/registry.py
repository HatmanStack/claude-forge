"""Registry + parsing helpers for the Claude Forge evaluation harness.

Pure-stdlib (no PyYAML, no network, no LLM) so Tier A / Tier C run fast and
deterministically on every pull request. This module is the single source of
truth for Forge's structural contracts: the role taxonomy, the tool policy per
role class, and the governance-signal authorization map. Tests assert the repo
against these; the trace hook keeps its own copy and Tier A cross-checks the two
agree.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
SKILLS_DIR = REPO_ROOT / "skills"
PROTOCOL = SKILLS_DIR / "pipeline" / "pipeline-protocol.md"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# ---- Role taxonomy (the team) --------------------------------------------------
GENERATOR_ROLES = {"planner", "implementer", "health-hygienist", "health-fortifier", "doc-engineer"}
REVIEWER_ROLES = {"plan-reviewer", "reviewer", "final-reviewer", "health-reviewer", "doc-reviewer"}
ASSESSOR_ROLES = {"eval-hire", "eval-stress", "eval-day2", "health-auditor", "doc-auditor"}
EXPECTED_ROLES = GENERATOR_ROLES | REVIEWER_ROLES | ASSESSOR_ROLES

# ---- Tool policy ---------------------------------------------------------------
# Allowed tool vocabulary across all Forge agents. Anything outside this set in a
# frontmatter `tools` list is a typo or an unintended grant.
ALLOWED_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "Bash"}
# Every role reads and runs commands; only the read-only quartet is universal.
REQUIRED_TOOLS_ALL = {"Read", "Glob", "Grep", "Bash"}
# No role may spawn nested agents (enforces the no-nesting constraint).
FORBIDDEN_TOOLS_ALL = {"Agent"}

# ---- Governance signals (DP3 provenance / Tier C trajectory) -------------------
# Gate-passing signals and the ONLY roles authorized to cast them.
ADVANCE_EMITTERS = {
    "PLAN_APPROVED": {"plan-reviewer"},
    "PHASE_APPROVED": {"reviewer", "health-reviewer", "doc-reviewer"},
    "GO": {"final-reviewer"},
    "VERIFIED": {"reviewer"},  # verification-reviewer is spawned as subagent_type reviewer
}
NEG_SIGNALS = {"REVISION_REQUIRED", "CHANGES_REQUESTED", "UNVERIFIED", "NO-GO"}
COMPLETE_SIGNALS = {"PLAN_COMPLETE", "IMPLEMENTATION_COMPLETE"}


def role_class(role):
    if role in GENERATOR_ROLES:
        return "generator"
    if role in REVIEWER_ROLES:
        return "reviewer"
    if role in ASSESSOR_ROLES:
        return "assessor"
    return "unknown"


# ---- Frontmatter parsing -------------------------------------------------------

def parse_frontmatter(text):
    """Minimal flat-YAML frontmatter parser for Forge agent files. Returns a dict
    of the leading `---` block (keys are single-line `key: value`). `tools` is a
    raw string; use `tool_list` to split it."""
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm


def tool_list(fm):
    raw = fm.get("tools", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def load_agents():
    """Return a list of {name, file, stem, description, tools, frontmatter} for
    every markdown file in agents/."""
    agents = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        text = path.read_text()
        fm = parse_frontmatter(text)
        agents.append({
            "name": fm.get("name", ""),
            "file": path,
            "stem": path.stem,
            "description": fm.get("description", ""),
            "tools": tool_list(fm),
            "frontmatter": fm,
        })
    return agents


# ---- Wiring / reference scanning ----------------------------------------------

_FORGE_REF_RE = re.compile(r"forge:([a-z0-9-]+)")


def scan_skill_refs():
    """Every `forge:<type>` referenced anywhere under skills/. Returns a set of
    bare role names (prefix stripped)."""
    refs = set()
    for path in SKILLS_DIR.rglob("*.md"):
        for m in _FORGE_REF_RE.finditer(path.read_text()):
            refs.add(m.group(1))
    return refs


def protocol_refs():
    """`forge:<type>` references in pipeline-protocol.md (the role->subagent_type
    contract). Returns a set of bare role names."""
    if not PROTOCOL.exists():
        return set()
    return {m.group(1) for m in _FORGE_REF_RE.finditer(PROTOCOL.read_text())}


# ---- Manifest / version helpers -----------------------------------------------

def plugin_manifest():
    return json.loads(PLUGIN_JSON.read_text())


def marketplace_manifest():
    return json.loads(MARKETPLACE_JSON.read_text())


def plugin_version():
    return plugin_manifest().get("version", "")


def marketplace_version():
    plugins = marketplace_manifest().get("plugins", [])
    return plugins[0].get("version", "") if plugins else ""


def changelog_has_version(version):
    if not CHANGELOG.exists():
        return False
    return f"## [{version}]" in CHANGELOG.read_text()
