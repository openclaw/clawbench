"""Ablation profiles and fair-comparison helpers.

The benchmark can only explain model, harness, and tool effects if those
axes are represented explicitly in run metadata. This module keeps that
representation small and deterministic: a harness driver plus a tool
profile yields a fingerprint, and result comparison refuses to call a
delta fair when models or task sets drift.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field

from clawbench.adapters import get_adapter
from clawbench.adapters.base import AdapterConfig
from clawbench.canonical import AdapterCapability
from clawbench.canonical.convert import from_task_definition
from clawbench.schemas import BenchmarkResult, TaskDefinition


CAPABILITY_TO_INTERFACE: dict[AdapterCapability, str] = {
    AdapterCapability.FILES: "filesystem",
    AdapterCapability.EXECUTION: "shell",
    AdapterCapability.MEMORY: "memory",
    AdapterCapability.SESSION: "session",
    AdapterCapability.CRON: "scheduler",
    AdapterCapability.BROWSER: "browser",
    AdapterCapability.GATEWAY_RPC: "gateway_rpc",
    AdapterCapability.MULTI_TURN_INJECTION: "multi_turn",
}


class HarnessDescriptor(BaseModel):
    """Identifies the agent loop being measured."""

    adapter: str
    driver: str = ""
    version: str = ""
    git_sha: str = ""
    source: str = ""
    invocation: str = "clawbench"


class ToolProfile(BaseModel):
    """The tools/interfaces exposed to a harness run."""

    name: str
    mode: str = "native"
    interfaces: list[str] = Field(default_factory=list)
    adapter_capabilities: list[str] = Field(default_factory=list)
    enabled_toolsets: list[str] = Field(default_factory=list)
    disabled_toolsets: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    fingerprint: str = ""

    def with_fingerprint(self) -> "ToolProfile":
        payload = {
            "name": self.name,
            "mode": self.mode,
            "interfaces": sorted(self.interfaces),
            "adapter_capabilities": sorted(self.adapter_capabilities),
            "enabled_toolsets": sorted(self.enabled_toolsets),
            "disabled_toolsets": sorted(self.disabled_toolsets),
            "tools": sorted(self.tools),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self.model_copy(update={"fingerprint": digest[:16]})


class AblationProfile(BaseModel):
    """Run-level axis metadata embedded in BenchmarkResult.environment."""

    model: str
    harness: HarnessDescriptor
    tool_profile: ToolProfile
    prompt_profile: str = "clear"
    fingerprint: str = ""

    def with_fingerprint(self) -> "AblationProfile":
        tool_profile = self.tool_profile.with_fingerprint()
        payload = {
            "model": self.model,
            "harness": self.harness.model_dump(),
            "tool_profile": tool_profile.model_dump(),
            "prompt_profile": self.prompt_profile,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self.model_copy(
            update={
                "tool_profile": tool_profile,
                "fingerprint": digest[:16],
            }
        )


@dataclass(frozen=True)
class FairTaskSet:
    task_ids: list[str]
    skipped: dict[str, list[str]] = field(default_factory=dict)


def capabilities_to_interfaces(capabilities: Iterable[AdapterCapability | str]) -> list[str]:
    values: list[str] = []
    for cap in capabilities:
        enum_value = cap if isinstance(cap, AdapterCapability) else AdapterCapability(str(cap))
        values.append(CAPABILITY_TO_INTERFACE.get(enum_value, enum_value.value))
    return sorted(set(values))


def adapter_capabilities(
    adapter: str,
    config: AdapterConfig | None = None,
) -> set[AdapterCapability]:
    adapter_cls = get_adapter(adapter)
    return adapter_cls.supported_capabilities(config)


def default_tool_profile(
    *,
    adapter: str,
    config: AdapterConfig | None = None,
    name: str | None = None,
    mode: str = "native",
    enabled_toolsets: list[str] | None = None,
    disabled_toolsets: list[str] | None = None,
) -> ToolProfile:
    caps = adapter_capabilities(adapter, config)
    profile = ToolProfile(
        name=name or f"{adapter}-{mode}",
        mode=mode,
        interfaces=capabilities_to_interfaces(caps),
        adapter_capabilities=sorted(cap.value for cap in caps),
        enabled_toolsets=enabled_toolsets or [],
        disabled_toolsets=disabled_toolsets or [],
    )
    return profile.with_fingerprint()


def compatible_task_ids(
    tasks: Iterable[TaskDefinition],
    *,
    adapter: str,
    config: AdapterConfig | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    caps = adapter_capabilities(adapter, config)
    task_ids: list[str] = []
    skipped: dict[str, list[str]] = {}
    for task in tasks:
        canonical = from_task_definition(task)
        missing = set(canonical.required_adapter_capabilities) - caps
        if missing:
            skipped[task.id] = sorted(cap.value for cap in missing)
        else:
            task_ids.append(task.id)
    return task_ids, skipped


def common_compatible_task_set(
    tasks: Iterable[TaskDefinition],
    adapter_configs: dict[str, tuple[str, AdapterConfig | None]],
) -> FairTaskSet:
    task_list = list(tasks)
    common: set[str] | None = None
    skipped: dict[str, list[str]] = {}
    for label, (adapter, config) in adapter_configs.items():
        ids, missing = compatible_task_ids(task_list, adapter=adapter, config=config)
        ids_set = set(ids)
        common = ids_set if common is None else common & ids_set
        for task_id, caps in missing.items():
            skipped.setdefault(task_id, []).append(f"{label}: {', '.join(caps)}")
    ordered = [task.id for task in task_list if task.id in (common or set())]
    return FairTaskSet(task_ids=ordered, skipped=skipped)


def build_ablation_profile(
    *,
    model: str,
    adapter: str,
    config: AdapterConfig | None = None,
    prompt_profile: str = "clear",
    harness_version: str = "",
    harness_git_sha: str = "",
    harness_source: str = "",
    driver: str = "",
    tool_profile_name: str | None = None,
    enabled_toolsets: list[str] | None = None,
    disabled_toolsets: list[str] | None = None,
) -> AblationProfile:
    harness = HarnessDescriptor(
        adapter=adapter,
        driver=driver,
        version=harness_version,
        git_sha=harness_git_sha,
        source=harness_source,
    )
    tool_profile = default_tool_profile(
        adapter=adapter,
        config=config,
        name=tool_profile_name,
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
    )
    return AblationProfile(
        model=model,
        harness=harness,
        tool_profile=tool_profile,
        prompt_profile=prompt_profile,
    ).with_fingerprint()


def compare_results(results: dict[str, BenchmarkResult]) -> dict[str, Any]:
    """Return score deltas plus fairness checks for result JSONs."""

    labels = list(results)
    models = {label: result.model for label, result in results.items()}
    task_sets = {
        label: [task.task_id for task in result.task_results]
        for label, result in results.items()
    }
    first_tasks = next(iter(task_sets.values()), [])
    same_task_set = all(tasks == first_tasks for tasks in task_sets.values())
    same_model = len(set(models.values())) == 1
    snapshot_fingerprints = {
        result.task_snapshot_fingerprint
        for result in results.values()
        if result.task_snapshot_fingerprint
    }
    same_task_snapshot = len(snapshot_fingerprints) <= 1
    prompt_variants = {
        str(result.environment.get("prompt_variant", ""))
        for result in results.values()
        if result.environment.get("prompt_variant", "")
    }
    same_prompt_variant = len(prompt_variants) <= 1
    benchmark_releases = {
        result.benchmark_release_id
        for result in results.values()
        if result.benchmark_release_id
    }
    same_benchmark_release = len(benchmark_releases) <= 1
    task_verifier_fair = same_task_set and same_task_snapshot and same_prompt_variant and same_benchmark_release

    rows: dict[str, Any] = {}
    for label, result in results.items():
        rows[label] = {
            "model": result.model,
            "adapter": result.environment.get("adapter", ""),
            "score": result.overall_score,
            "completion": result.overall_completion,
            "trajectory": result.overall_trajectory,
            "behavior": result.overall_behavior,
            "reliability": result.overall_reliability,
            "task_count": len(result.task_results),
            "task_snapshot_fingerprint": result.task_snapshot_fingerprint,
            "benchmark_release_id": result.benchmark_release_id,
            "prompt_variant": result.environment.get("prompt_variant", ""),
            "dimension_coverage": result.environment.get("dimension_coverage", {}),
            "ablation": result.environment.get("ablation_profile", {}),
        }

    deltas: dict[str, float] = {}
    if labels:
        baseline = results[labels[0]].overall_score
        for label in labels[1:]:
            deltas[f"{label}_minus_{labels[0]}"] = round(
                results[label].overall_score - baseline,
                4,
            )

    return {
        "fair": bool(task_verifier_fair),
        "task_verifier_fair": bool(task_verifier_fair),
        "controlled_ablation": bool(task_verifier_fair and same_model),
        "same_model": same_model,
        "same_task_set": same_task_set,
        "same_task_snapshot": same_task_snapshot,
        "same_prompt_variant": same_prompt_variant,
        "same_benchmark_release": same_benchmark_release,
        "models": models,
        "task_sets": task_sets,
        "rows": rows,
        "deltas": deltas,
    }


def git_head(path: Path) -> tuple[str, str]:
    """Best-effort `(sha, describe)` for harness provenance."""

    try:
        sha = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        desc = subprocess.check_output(
            ["git", "-C", str(path), "describe", "--tags", "--always", "--dirty"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return sha, desc
    except Exception:
        return "", ""
