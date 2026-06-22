"""Tier A — agent frontmatter + tool-policy contracts.

Deterministic, no LLM, no network. Registry-driven: new agents in agents/ are
picked up automatically. These freeze the invariants established when the team
was migrated to native subagents so they can't silently rot.
"""
import re

import pytest

from lib import registry

AGENTS = registry.load_agents()
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Parametrize per agent so a failure names the offending file.
_ids = [a["stem"] for a in AGENTS]


def test_agents_directory_is_not_empty():
    assert AGENTS, "no agent files found under agents/"


def test_expected_roles_exactly_present():
    """The team is exactly the 15 expected roles — no missing, no stray files."""
    found = {a["name"] for a in AGENTS}
    assert found == registry.EXPECTED_ROLES, (
        f"missing={registry.EXPECTED_ROLES - found} unexpected={found - registry.EXPECTED_ROLES}"
    )


def test_agent_names_unique():
    names = [a["name"] for a in AGENTS]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate agent name(s): {dupes}"


@pytest.mark.parametrize("agent", AGENTS, ids=_ids)
def test_required_frontmatter_present(agent):
    for field in ("name", "description", "tools"):
        assert agent["frontmatter"].get(field), f"{agent['stem']}.md missing frontmatter field: {field}"


@pytest.mark.parametrize("agent", AGENTS, ids=_ids)
def test_name_format_and_matches_filename(agent):
    name = agent["name"]
    assert NAME_RE.match(name), f"{name!r} must be lowercase letters/digits with single hyphens"
    assert name == agent["stem"], f"frontmatter name {name!r} != filename {agent['stem']!r}"


@pytest.mark.parametrize("agent", AGENTS, ids=_ids)
def test_tools_within_allowed_vocabulary(agent):
    extra = set(agent["tools"]) - registry.ALLOWED_TOOLS
    assert not extra, f"{agent['stem']} grants tools outside the allowed set: {extra}"


@pytest.mark.parametrize("agent", AGENTS, ids=_ids)
def test_no_agent_tool_no_nesting(agent):
    forbidden = set(agent["tools"]) & registry.FORBIDDEN_TOOLS_ALL
    assert not forbidden, f"{agent['stem']} must not grant {forbidden} (no nested spawning)"


@pytest.mark.parametrize("agent", AGENTS, ids=_ids)
def test_universal_read_only_tools_present(agent):
    missing = registry.REQUIRED_TOOLS_ALL - set(agent["tools"])
    assert not missing, f"{agent['stem']} is missing required tools: {missing}"


@pytest.mark.parametrize("agent", AGENTS, ids=_ids)
def test_tool_policy_per_role_class(agent):
    name, tools = agent["name"], set(agent["tools"])
    cls = registry.role_class(name)
    if cls == "generator":
        assert "Write" in tools and "Edit" in tools, f"generator {name} must have Write+Edit"
    elif cls == "reviewer":
        assert "Edit" in tools, f"reviewer {name} must have Edit (for feedback.md)"
        assert "Write" not in tools, f"reviewer {name} must NOT have Write (read-only over source)"
    elif cls == "assessor":
        assert "Write" not in tools and "Edit" not in tools, (
            f"assessor {name} must be read-only (no Write/Edit)"
        )
    else:
        pytest.fail(f"{name} is not classified into a known role class")
