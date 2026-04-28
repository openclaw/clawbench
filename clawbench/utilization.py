"""ClawBench v0.5 — Plugin Utilization Audit and Manifest-vs-Reality Gap.

This module answers two questions from the Configuration Diagnostic Report
(CLAWBENCH_V0_4_SPEC.md §"Configuration Diagnostic Report" items 3 and 4):

  3. For each plugin in the profile, was it actually invoked during the
     run? Plugins that loaded but were never called are flagged as dead
     weight.

  4. For each plugin, did it impact the tasks its manifest suggested it
     would? Discrepancies are listed.

Both are computed purely from the profile + transcripts, with no live
gateway instrumentation required. The tool-name → plugin-id mapping is
derived from the RegistrationTrace when available, and falls back to a
conservative heuristic (tool family match) when the trace is missing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, asdict

from clawbench.profile import (
    PluginManifest,
    PluginProfile,
    RegistrationTrace,
)
from clawbench.schemas import Transcript
from clawbench.trajectory import classify_tool_call


@dataclass
class PluginUtilization:
    """Per-plugin invocation summary for a single profile run."""

    plugin_id: str
    source: str
    invoked: bool
    invocation_count: int
    tool_calls: list[str] = field(default_factory=list)  # tool names invoked
    tool_families_touched: list[str] = field(default_factory=list)
    task_ids_with_invocation: list[str] = field(default_factory=list)
    dead_weight: bool = False  # True if plugin loaded but never invoked

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UtilizationReport:
    n_plugins: int
    n_invoked: int
    n_dead_weight: int
    per_plugin: list[PluginUtilization] = field(default_factory=list)
    unassigned_tool_calls: int = 0  # tool calls we couldn't trace back to a plugin

    def to_dict(self) -> dict:
        return {
            "n_plugins": self.n_plugins,
            "n_invoked": self.n_invoked,
            "n_dead_weight": self.n_dead_weight,
            "unassigned_tool_calls": self.unassigned_tool_calls,
            "per_plugin": [p.to_dict() for p in self.per_plugin],
        }

    @property
    def utilization_rate(self) -> float:
        if self.n_plugins == 0:
            return 0.0
        return self.n_invoked / self.n_plugins


def _build_tool_to_plugin_map(
    profile: PluginProfile,
    traces: dict[str, RegistrationTrace] | None,
) -> dict[str, str]:
    """Map tool name → plugin_id using registration traces when available."""
    mapping: dict[str, str] = {}
    if not traces:
        return mapping
    for entry in profile.plugins:
        trace = traces.get(entry.id)
        if trace is None:
            continue
        for tool_name in trace.tools:
            # First-registration wins; traces are processed in profile order
            mapping.setdefault(tool_name, entry.id)
    return mapping


def _fallback_family_to_plugin(
    profile: PluginProfile,
    traces: dict[str, RegistrationTrace] | None,
) -> dict[str, list[str]]:
    """Fallback: map tool family → candidate plugin ids.

    Used when a tool call's name does not appear in any registration trace
    (e.g., because no traces were captured for this run). We can still
    attribute at the family level based on what each plugin declared.
    """
    out: dict[str, list[str]] = {}
    if not traces:
        return out
    for entry in profile.plugins:
        trace = traces.get(entry.id)
        if trace is None:
            continue
        for fam in trace.tool_families_seen:
            out.setdefault(fam, []).append(entry.id)
    return out


def audit_plugin_utilization(
    profile: PluginProfile,
    transcripts: dict[str, Transcript],
    *,
    manifests: dict[str, PluginManifest] | None = None,
    traces: dict[str, RegistrationTrace] | None = None,
) -> UtilizationReport:
    """Compute a UtilizationReport from a profile + per-task transcripts.

    Parameters
    ----------
    profile : PluginProfile
        The submitted profile.
    transcripts : dict[task_id, Transcript]
        The per-task transcripts from the v0.4 benchmark run.
    manifests : dict[plugin_id, PluginManifest] | None
        Optional cached manifests (unused directly but kept for parity
        with other v0.5 signatures — callers always have them around).
    traces : dict[plugin_id, RegistrationTrace] | None
        Optional registration traces. When provided, enables exact tool
        name → plugin_id attribution. When missing, falls back to family
        matching.

    Returns
    -------
    UtilizationReport
    """
    del manifests  # accepted for signature parity; not currently needed

    tool_to_plugin = _build_tool_to_plugin_map(profile, traces)
    family_to_plugins = _fallback_family_to_plugin(profile, traces)

    per_plugin_counts: dict[str, int] = {e.id: 0 for e in profile.plugins}
    per_plugin_tools: dict[str, Counter] = {e.id: Counter() for e in profile.plugins}
    per_plugin_families: dict[str, set[str]] = {e.id: set() for e in profile.plugins}
    per_plugin_tasks: dict[str, set[str]] = {e.id: set() for e in profile.plugins}
    unassigned = 0

    for task_id, transcript in transcripts.items():
        for call in transcript.tool_call_sequence:
            family = call.family or classify_tool_call(call)[0] or "unknown"
            plugin_id = tool_to_plugin.get(call.name)
            if plugin_id is None:
                # Family fallback: if exactly one plugin claims this family,
                # attribute to it. If multiple do, leave unassigned — we
                # don't want to inflate counts via ambiguous attribution.
                candidates = family_to_plugins.get(family, [])
                if len(candidates) == 1:
                    plugin_id = candidates[0]
            if plugin_id is None or plugin_id not in per_plugin_counts:
                unassigned += 1
                continue
            per_plugin_counts[plugin_id] += 1
            per_plugin_tools[plugin_id][call.name] += 1
            per_plugin_families[plugin_id].add(family)
            per_plugin_tasks[plugin_id].add(task_id)

    per_plugin: list[PluginUtilization] = []
    for entry in profile.plugins:
        count = per_plugin_counts[entry.id]
        invoked = count > 0
        per_plugin.append(PluginUtilization(
            plugin_id=entry.id,
            source=entry.source,
            invoked=invoked,
            invocation_count=count,
            tool_calls=sorted(per_plugin_tools[entry.id].keys()),
            tool_families_touched=sorted(per_plugin_families[entry.id]),
            task_ids_with_invocation=sorted(per_plugin_tasks[entry.id]),
            dead_weight=not invoked,
        ))

    n_invoked = sum(1 for p in per_plugin if p.invoked)
    n_dead = sum(1 for p in per_plugin if p.dead_weight)

    return UtilizationReport(
        n_plugins=len(per_plugin),
        n_invoked=n_invoked,
        n_dead_weight=n_dead,
        per_plugin=per_plugin,
        unassigned_tool_calls=unassigned,
    )


# ---------------------------------------------------------------------------
# Manifest vs. Reality Gap — §4 of the Configuration Diagnostic Report.
# ---------------------------------------------------------------------------


@dataclass
class ManifestRealityGap:
    plugin_id: str
    claimed_capabilities: list[str]
    observed_capabilities: list[str]
    unused_capabilities: list[str]  # claimed but never exercised
    unclaimed_capabilities: list[str]  # observed but not declared
    claim_coverage: float  # fraction of claimed capabilities actually exercised

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ManifestRealityReport:
    per_plugin: list[ManifestRealityGap] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"per_plugin": [g.to_dict() for g in self.per_plugin]}


def _manifest_claimed_families(manifest: PluginManifest) -> set[str]:
    """Derive claimed tool families from a manifest.

    Each manifest contract maps to one or more ClawBench tool families.
    This mapping is conservative: if we're not sure, we don't claim the
    family. The point is to detect mismatch, not to be exhaustive.
    """
    claimed: set[str] = set()
    contracts = manifest.contracts or {}
    if contracts.get("tools"):
        # Can't determine family from contract alone, but it's "something"
        claimed.add("unknown")
    if contracts.get("webFetchProviders") or contracts.get("webSearchProviders"):
        claimed.add("search")
        claimed.add("browser")
    if "memory" in manifest.kind:
        claimed.add("memory")
    if "context-engine" in manifest.kind:
        claimed.add("read")
    return claimed


def compute_manifest_reality_gap(
    profile: PluginProfile,
    manifests: dict[str, PluginManifest],
    utilization: UtilizationReport,
) -> ManifestRealityReport:
    """For each plugin, compare what the manifest claims against what ran."""
    gaps: list[ManifestRealityGap] = []
    util_lookup = {p.plugin_id: p for p in utilization.per_plugin}

    for entry in profile.plugins:
        manifest = manifests.get(entry.id)
        if manifest is None:
            continue
        util = util_lookup.get(entry.id)
        claimed = _manifest_claimed_families(manifest)
        observed = set(util.tool_families_touched) if util else set()
        # Drop the "unknown" sentinel from both sides when computing coverage
        claimed_concrete = claimed - {"unknown"}
        unused = sorted(claimed_concrete - observed)
        unclaimed = sorted(observed - claimed_concrete - {"unknown"})
        if claimed_concrete:
            coverage = len(claimed_concrete & observed) / len(claimed_concrete)
        else:
            # Plugin made no family-level claims — coverage is 1.0 if it
            # was invoked at all, else 0.0.
            coverage = 1.0 if (util and util.invoked) else 0.0
        gaps.append(ManifestRealityGap(
            plugin_id=entry.id,
            claimed_capabilities=sorted(claimed_concrete),
            observed_capabilities=sorted(observed),
            unused_capabilities=unused,
            unclaimed_capabilities=unclaimed,
            claim_coverage=round(coverage, 4),
        ))

    return ManifestRealityReport(per_plugin=gaps)
