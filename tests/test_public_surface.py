import re
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_ID_RE = re.compile(r"\bt[1-6]-[a-z0-9-]+")


def _public_task_ids() -> set[str]:
    manifest = yaml.safe_load((REPO_ROOT / "tasks-public" / "MANIFEST.yaml").read_text(encoding="utf-8"))
    return {task["id"] for task in manifest["tasks"]}


def _mentioned_task_ids(path: Path) -> set[str]:
    return set(TASK_ID_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))


def _tracked_script_files() -> list[Path]:
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "scripts"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return [
            path
            for path in sorted((REPO_ROOT / "scripts").glob("*"))
            if path.is_file() and path.suffix in {".py", ".sh"}
        ]
    return [
        REPO_ROOT / line
        for line in output.splitlines()
        if Path(line).suffix in {".py", ".sh"}
    ]


def test_public_docs_only_reference_public_task_ids():
    public_ids = _public_task_ids()
    docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "SPACE_README.md",
        REPO_ROOT / "tasks-public" / "README.md",
        REPO_ROOT / "tasks-public" / "MANIFEST.yaml",
    ]

    leaked: dict[str, list[str]] = {}
    for path in docs:
        private_mentions = sorted(_mentioned_task_ids(path) - public_ids)
        if private_mentions:
            leaked[str(path.relative_to(REPO_ROOT))] = private_mentions

    assert leaked == {}


def test_reusable_scripts_do_not_embed_private_task_ids():
    public_ids = _public_task_ids()
    leaked: dict[str, list[str]] = {}

    for path in _tracked_script_files():
        private_mentions = sorted(_mentioned_task_ids(path) - public_ids)
        if private_mentions:
            leaked[str(path.relative_to(REPO_ROOT))] = private_mentions

    assert leaked == {}


def test_public_docs_match_manifest_task_count():
    manifest = yaml.safe_load((REPO_ROOT / "tasks-public" / "MANIFEST.yaml").read_text(encoding="utf-8"))
    task_count = int(manifest["task_count"])
    assert task_count == len(manifest["tasks"]) == 19

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    space_readme = (REPO_ROOT / "SPACE_README.md").read_text(encoding="utf-8")

    assert f"Core v1: {task_count} tasks" in readme
    assert "tasks          : 19" in space_readme
    assert f"Core v1: {task_count + 8} tasks" not in readme
    assert f"tasks          : {task_count + 1}" not in space_readme
