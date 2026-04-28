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
    # The agent must surface the Q3 marketing budget content. The desktop
    # copy is the explicit target, but accept any file the agent created
    # that contains the right content (Q3 marketing + region breakdowns).
    target_substrings = ["q3", "region"]
    decoy_q2 = ["q2 marketing", "q2 spend"]
    decoy_sales = ["q3 revenue", "q3 sales"]

    found_path = None
    for path, text in iter_workspace_text_files():
        # Skip the original asset-pack files (we want files the agent
        # *placed* somewhere — typically a desktop/copy or report)
        if "/Documents/" in str(path) and "v3" in path.name:
            continue
        text_lower = text.lower()
        if all(s in text_lower for s in target_substrings) and "marketing" in text_lower:
            # Reject decoys
            if any(d in text_lower for d in decoy_q2):
                continue
            if any(d in text_lower for d in decoy_sales):
                continue
            found_path = path
            break

    # Also accept agent text output (e.g. answer.md) that just NAMES the
    # right file
    if found_path is None:
        for path, text in iter_workspace_text_files():
            if "q3_marketing_budget_v3" in text.lower():
                found_path = path
                break

    if found_path is None:
        print("FAIL: agent did not surface the correct Q3 marketing budget file")
        return 1
    print(f"PASS: agent surfaced Q3 marketing budget content at/in {found_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
