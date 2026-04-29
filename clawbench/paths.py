"""Path helpers for task-owned workspace references."""

from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(workspace: Path, path: str, *, field: str = "path") -> Path:
    """Resolve a task-declared path and reject workspace escapes."""
    root = workspace.resolve()
    candidate = (workspace / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field} escapes workspace: {path}") from exc
    return candidate
