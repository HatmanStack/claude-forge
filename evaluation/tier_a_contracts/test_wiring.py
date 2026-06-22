"""Tier A — wiring contracts: skill references, protocol table, manifests, and
hook/registry agreement. Deterministic, no LLM, no network.
"""
import importlib.util

from lib import registry


def test_every_referenced_type_resolves_to_an_agent():
    """Every `forge:<type>` the skills/flows spawn must map to an agent file."""
    refs = registry.scan_skill_refs()
    have = {a["name"] for a in registry.load_agents()}
    dangling = refs - have
    assert not dangling, f"skills reference forge types with no agent file: {dangling}"


def test_no_orphan_agents():
    """Every agent is referenced somewhere in skills/ (catches a rename that
    leaves a file no orchestrator spawns)."""
    refs = registry.scan_skill_refs()
    have = {a["name"] for a in registry.load_agents()}
    orphans = have - refs
    assert not orphans, f"agent files never referenced by any skill: {orphans}"


def test_protocol_table_lists_all_roles():
    """pipeline-protocol.md is the role->subagent_type contract; it must mention
    every role."""
    refs = registry.protocol_refs()
    missing = registry.EXPECTED_ROLES - refs
    assert not missing, f"roles missing from pipeline-protocol.md: {missing}"


def test_plugin_does_not_declare_agents_field():
    """A `agents` field in plugin.json REPLACES the default agents/ scan; we rely
    on auto-discovery, so it must be absent."""
    assert "agents" not in registry.plugin_manifest(), (
        "plugin.json must not declare an `agents` field (it would disable auto-discovery)"
    )


def test_plugin_and_marketplace_versions_agree():
    assert registry.plugin_version() == registry.marketplace_version(), (
        f"plugin.json {registry.plugin_version()} != marketplace.json {registry.marketplace_version()}"
    )


def test_changelog_has_current_version():
    v = registry.plugin_version()
    assert registry.changelog_has_version(v), f"CHANGELOG.md has no entry for version {v}"


def test_trace_hook_imports_and_role_sets_match_registry():
    """The deployed trace hook keeps its own role taxonomy (it ships standalone).
    Cross-check it agrees with the registry so security/eval and the agents can't
    drift apart."""
    hook_path = registry.REPO_ROOT / "hooks" / "trace_subagents.py"
    spec = importlib.util.spec_from_file_location("forge_trace_hook", hook_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # stdlib-only imports; safe without opentelemetry

    assert mod._GENERATOR_ROLES == registry.GENERATOR_ROLES
    assert mod._REVIEWER_ROLES == registry.REVIEWER_ROLES
    assert mod._ASSESSOR_ROLES == registry.ASSESSOR_ROLES
    assert mod._ADVANCE_EMITTERS == registry.ADVANCE_EMITTERS
