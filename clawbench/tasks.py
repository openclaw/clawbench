"""Task loading helpers for the ClawBench task corpus."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from clawbench.releases import get_active_hidden_tasks_dir
from clawbench.query_catalog import apply_query_metadata_overrides
from clawbench.schemas import TaskDefinition


def _resolve_tasks_dir() -> Path:
    """Resolve the tasks directory at import time.

    When ClawBench is run from a private source checkout, `tasks/` is a
    sibling of the `clawbench/` package directory. Public checkouts and the
    HF Space Docker image ship `tasks-public/` instead. This resolver tries a
    series of candidates in order and falls back to the sibling-of-source path
    so private source runs stay unaffected.
    """
    # 1. Explicit override via environment variable.
    env_dir = os.environ.get("CLAWBENCH_TASKS_DIR", "").strip()
    if env_dir:
        candidate = Path(env_dir).expanduser().resolve()
        if (candidate / "tier1").is_dir() or candidate.glob("tier*"):
            return candidate

    # 2. Sibling of the package source (works for source checkouts).
    sibling = Path(__file__).parent.parent / "tasks"
    if (sibling / "tier1").is_dir():
        return sibling

    # 3. Current working directory (works when the user runs clawbench from
    #    a private repo root that has tasks/ in it).
    cwd_dir = Path.cwd() / "tasks"
    if (cwd_dir / "tier1").is_dir():
        return cwd_dir

    # 4. Known private Docker/HF Space layout.
    for container_candidate in (
        Path("/home/node/app/tasks"),
        Path("/home/user/app/tasks"),
        Path("/app/tasks"),
    ):
        if (container_candidate / "tier1").is_dir():
            return container_candidate

    # 5. Fall back to the public task release (tasks-public/) if present.
    #    This lets CI / external contributors run the test suite without
    #    the private dev-only tasks/ directory. The public Core release
    #    uses the same on-disk layout as the private set.
    for public_candidate in (
        Path(__file__).parent.parent / "tasks-public",
        Path.cwd() / "tasks-public",
        Path("/home/node/app/tasks-public"),
        Path("/home/user/app/tasks-public"),
        Path("/app/tasks-public"),
    ):
        if (public_candidate / "tier1").is_dir():
            return public_candidate

    # 6. Give up and return the sibling path anyway — task loading will
    #    fail loudly instead of silently returning an empty task list.
    return sibling


TASKS_DIR = _resolve_tasks_dir()


def load_task(path: Path) -> TaskDefinition:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    data = apply_query_metadata_overrides(data)
    return TaskDefinition(**data)


def load_all_tasks(
    *,
    tasks_dir: Path | None = None,
    tier: str | None = None,
    task_ids: list[str] | None = None,
    scenario: str | None = None,
    artifact_type: str | None = None,
    prompt_variant: str | None = None,
    pool: str | None = None,
    subsets: list[str] | None = None,
    capabilities: list[str] | None = None,
    official_only: bool = False,
    include_private: bool = False,
) -> list[TaskDefinition]:
    requested_pool = (pool or "").strip().lower()
    roots: list[Path]
    if tasks_dir is not None:
        roots = [tasks_dir]
    elif requested_pool == "official_hidden":
        hidden_root = get_active_hidden_tasks_dir()
        roots = [hidden_root] if hidden_root is not None else []
    else:
        roots = [TASKS_DIR]
        if include_private:
            hidden_root = get_active_hidden_tasks_dir()
            if hidden_root is not None:
                roots.append(hidden_root)
    tasks: list[TaskDefinition] = []
    seen: set[tuple[str, str, str, str]] = set()
    requested_subsets = {item.lower() for item in (subsets or [])}
    requested_capabilities = {item.lower() for item in (capabilities or [])}
    for root in roots:
        tier_roots = sorted(path for path in root.glob("tier*") if path.is_dir())
        search_roots = tier_roots or [root]
        for search_root in search_roots:
            for yaml_path in sorted(search_root.rglob("*.yaml")):
                if yaml_path.name.startswith("_"):
                    continue
                task = load_task(yaml_path)
                if tier and task.tier.value != tier:
                    continue
                if task_ids and task.id not in task_ids:
                    continue
                if scenario and (task.scenario is None or task.scenario.value != scenario):
                    continue
                if artifact_type and (task.artifact_type is None or task.artifact_type.value != artifact_type):
                    continue
                if prompt_variant and prompt_variant not in {variant.value for variant in task.prompt_variants}:
                    continue
                if pool and task.pool.value != pool:
                    continue
                if official_only and not task.official:
                    continue
                if requested_subsets and not requested_subsets.intersection(subset.value for subset in task.subsets):
                    continue
                if requested_capabilities and not requested_capabilities.intersection(
                    capability.value for capability in task.capabilities
                ):
                    continue
                key = (task.id, task.pool.value, task.variant_id, task.release_id)
                if key in seen:
                    continue
                seen.add(key)
                tasks.append(task)
    return tasks


def get_assets_dir() -> Path:
    return TASKS_DIR / "assets"
