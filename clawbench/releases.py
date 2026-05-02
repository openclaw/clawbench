"""Helpers for managing rolling private benchmark releases."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from clawbench.schemas import TaskDefinition, TaskPool


class ActiveReleaseManifest(BaseModel):
    public_release_id: str = "public"
    hidden_release_id: str = ""
    benchmark_release_id: str = ""
    hidden_tasks_dir: str = ""
    task_ids: list[str] = Field(default_factory=list)
    task_snapshot_fingerprint: str = ""
    created_at: str = ""


class HiddenReleaseManifest(BaseModel):
    release_id: str
    created_at: str
    hidden_tasks_dir: str
    task_ids: list[str] = Field(default_factory=list)
    source_task_ids: list[str] = Field(default_factory=list)
    task_snapshot_fingerprint: str = ""


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_private_tasks_root() -> Path:
    raw = os.environ.get("CLAWBENCH_PRIVATE_TASKS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(".clawbench/private_tasks").resolve()


def get_registry_dir() -> Path:
    raw = os.environ.get("CLAWBENCH_REGISTRY_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(".clawbench/registry").resolve()


def get_active_release_path() -> Path:
    raw = os.environ.get("CLAWBENCH_ACTIVE_RELEASE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return get_registry_dir() / "active_release.json"


def get_hidden_release_dir(release_id: str, *, private_tasks_root: Path | None = None) -> Path:
    return (private_tasks_root or get_private_tasks_root()) / release_id


def compute_task_snapshot_fingerprint(tasks: list[TaskDefinition]) -> str:
    payload = [
        task.model_dump(mode="json", exclude_none=False)
        for task in sorted(
            tasks,
            key=lambda task: (
                task.id,
                task.pool.value,
                task.variant_group,
                task.variant_id,
                task.release_id,
            ),
        )
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_active_release(path: Path | None = None) -> ActiveReleaseManifest | None:
    manifest_path = path or get_active_release_path()
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return ActiveReleaseManifest(**data)


def write_active_release(manifest: ActiveReleaseManifest, path: Path | None = None) -> Path:
    manifest_path = path or get_active_release_path()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def get_active_hidden_tasks_dir() -> Path | None:
    manifest = load_active_release()
    if manifest is None or not manifest.hidden_tasks_dir:
        return None
    candidate = Path(manifest.hidden_tasks_dir).expanduser().resolve()
    tier_roots = [path for path in candidate.glob("tier*") if path.is_dir()]
    return candidate if tier_roots else None


def export_task_definition(task: TaskDefinition, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = task.model_dump(mode="json", exclude_none=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def materialize_hidden_release_tasks(tasks: list[TaskDefinition], *, release_id: str) -> list[TaskDefinition]:
    materialized: list[TaskDefinition] = []
    for task in tasks:
        cloned = task.model_copy(deep=True)
        cloned.pool = TaskPool.OFFICIAL_HIDDEN
        cloned.official = True
        cloned.release_id = release_id
        cloned.freshness_epoch = release_id
        if not cloned.first_used_at:
            cloned.first_used_at = now_utc_iso()
        if not cloned.variant_id or cloned.variant_id == "main":
            cloned.variant_id = release_id
        if not cloned.privacy_tier:
            cloned.privacy_tier = "private"
        if not cloned.contamination_risk:
            cloned.contamination_risk = "medium"
        if not cloned.source_kind:
            cloned.source_kind = "synthetic"
        if not cloned.template_id:
            cloned.template_id = cloned.variant_group or cloned.id
        if not cloned.provenance_ids:
            cloned.provenance_ids = [cloned.id]
        if not cloned.canary_token:
            cloned.canary_token = f"clawbench-canary::{release_id}::{cloned.id}"
        materialized.append(cloned)
    return materialized


def build_hidden_release(
    *,
    tasks: list[TaskDefinition],
    release_id: str,
    private_tasks_root: Path | None = None,
    activate: bool = True,
    active_release_path: Path | None = None,
) -> HiddenReleaseManifest:
    release_dir = get_hidden_release_dir(release_id, private_tasks_root=private_tasks_root)
    materialized = materialize_hidden_release_tasks(tasks, release_id=release_id)
    for task in materialized:
        export_task_definition(task, release_dir / task.tier.value / f"{task.id}.yaml")

    manifest = HiddenReleaseManifest(
        release_id=release_id,
        created_at=now_utc_iso(),
        hidden_tasks_dir=str(release_dir),
        task_ids=[task.id for task in materialized],
        source_task_ids=[task.id for task in tasks],
        task_snapshot_fingerprint=compute_task_snapshot_fingerprint(materialized),
    )
    (release_dir / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    if activate:
        active_manifest = ActiveReleaseManifest(
            public_release_id="public",
            hidden_release_id=release_id,
            benchmark_release_id=release_id,
            hidden_tasks_dir=str(release_dir),
            task_ids=manifest.task_ids,
            task_snapshot_fingerprint=manifest.task_snapshot_fingerprint,
            created_at=manifest.created_at,
        )
        write_active_release(active_manifest, path=active_release_path)

    return manifest
