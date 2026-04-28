"""Recursive workspace search verifier — checks that handoff.md (or any
alternate .md/.txt the agent wrote) captures all three flags.

This task tests multi-entry memory recall; each fact must appear in the
handoff artifact regardless of filename or formatting style."""

from __future__ import annotations

import sys
from pathlib import Path

EXCLUDE_FRAGMENTS = (
    "verify_", "/.git/", "/.openclaw/",
    "BOOTSTRAP.md", "IDENTITY.md", "AGENTS.md",
    "USER.md", "SOUL.md", "HEARTBEAT.md",
    "release_notes.md",  # don't count re-reads of the source doc
)
TEXT_SUFFIXES = (".md", ".txt", ".json", ".yaml", ".yml")


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
    if not blob.strip():
        print("FAIL: no agent-written text artifacts found in workspace")
        return 1

    # Fact 1: beta regions us + eu
    if "us" not in blob or "eu" not in blob:
        print("FAIL: handoff missing beta regions (expected 'us' and 'eu')")
        return 1

    # Fact 2: retry budget 3
    if "3" not in blob or "retry" not in blob:
        print("FAIL: handoff missing retry budget fact (expected '3' and 'retry')")
        return 1

    # Fact 3: APAC gated until 2026.3
    if "apac" not in blob or "2026.3" not in blob:
        print("FAIL: handoff missing APAC gating fact (expected 'apac' and '2026.3')")
        return 1

    print("PASS: handoff captures beta regions, retry budget, and APAC gating")
    return 0


if __name__ == "__main__":
    sys.exit(main())
