"""Recursive workspace search verifier."""

from __future__ import annotations

import sys
from pathlib import Path

EXCLUDE_FRAGMENTS = (
    "verify_", "/.git/", "/.openclaw/",
    "BOOTSTRAP.md", "IDENTITY.md", "AGENTS.md",
    "USER.md", "SOUL.md", "HEARTBEAT.md",
)
TEXT_SUFFIXES = (".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".log",
                  ".jsonl", ".html", ".sh", ".py")


def iter_workspace_text_files(root: Path = Path(".")):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        sp = str(path)
        if any(frag in sp for frag in EXCLUDE_FRAGMENTS):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            yield path, path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue


def workspace_blob() -> str:
    return "\n".join(text for _, text in iter_workspace_text_files())


import re

LIST_PATTERNS = [
    re.compile(r"^\s*[-*+]\s+"),
    re.compile(r"^\s*\d+[.)]\s+"),
    re.compile(r"^\s*\[[ x]\]\s+"),
]


def main() -> int:
    for path, text in iter_workspace_text_files():
        if any(t in text.lower() for t in ("dry clean", "sam", "babysit", "60")):
            list_lines = sum(1 for line in text.splitlines() if any(p.match(line) for p in LIST_PATTERNS))
            if list_lines >= 3:
                print(f"PASS: list-formatted note found at {path} ({list_lines} list lines)")
                return 0
    print("FAIL: no list-structured note found anywhere in workspace")
    return 1


if __name__ == "__main__":
    sys.exit(main())
