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


def main() -> int:
    blob = workspace_blob().lower()
    if not blob:
        print("FAIL: workspace contains no agent-written text files")
        return 1
    needed = ['fushimi inari']
    if not all(s in blob for s in needed):
        missing = [s for s in needed if s not in blob]
        print(f"FAIL: workspace missing required content: {missing}")
        return 1
    print("PASS: t4_life_trip_plan/verify_landmark_present.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
