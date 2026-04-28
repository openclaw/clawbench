#!/usr/bin/env python3
"""Create an annotated git checkpoint tag for a clean working tree."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run_git(args: list[str], repo_root: Path, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit("Not inside a git repository.") from exc
    return Path(result.stdout.strip())


def sanitize_label(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    if not slug:
        raise SystemExit("Checkpoint name must contain at least one letter or number.")
    return slug[:48]


def ensure_clean_worktree(root: Path) -> None:
    status = run_git(["status", "--porcelain"], root).stdout.strip()
    if status:
        raise SystemExit(
            "Working tree is not clean. Commit or stash your changes first, or rerun with --allow-dirty."
        )


def current_branch(root: Path) -> str:
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"], root).stdout.strip()


def tag_exists(root: Path, tag_name: str) -> bool:
    result = run_git(["tag", "--list", tag_name], root)
    return result.stdout.strip() == tag_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an annotated checkpoint tag for the current HEAD commit."
    )
    parser.add_argument("name", help="Human-readable checkpoint name, e.g. 'before benchmark rerun'.")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow tagging even if the working tree has uncommitted changes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the tag that would be created without modifying git state.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    root = repo_root()
    if not args.allow_dirty:
        ensure_clean_worktree(root)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = current_branch(root)
    slug = sanitize_label(args.name)
    tag_name = f"checkpoint/{timestamp}-{slug}"

    if tag_exists(root, tag_name):
        raise SystemExit(f"Checkpoint tag already exists: {tag_name}")

    message = f"Checkpoint '{args.name}' from branch '{branch}' at {timestamp}"

    if args.dry_run:
        print(tag_name)
        print(message)
        return 0

    run_git(["tag", "-a", tag_name, "-m", message], root, capture_output=False)
    print(f"Created {tag_name}")
    print(f"Push it with: git push origin {tag_name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            sys.stderr.write(exc.stderr)
        raise SystemExit(exc.returncode) from exc
