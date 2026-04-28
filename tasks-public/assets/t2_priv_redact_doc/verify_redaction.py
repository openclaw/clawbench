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


PII = [
    "Lin Park", "lin.park@personalmail.example",
    "+1 (415) 555-0173", "415 555-0173", "415-555-0173",
    "AC-77821-PK",
]
PRESERVED_KEYWORDS = ["service agreement", "scope of work", "termination"]


def main() -> int:
    # Find a redacted version of the contract somewhere
    for path, text in iter_workspace_text_files():
        if "contract" not in path.name.lower() or "redact" not in path.name.lower():
            continue
        leaked = [v for v in PII if v in text]
        if leaked:
            print(f"FAIL: redacted file {path} still contains PII: {leaked}")
            return 1
        text_lower = text.lower()
        if not all(k in text_lower for k in PRESERVED_KEYWORDS):
            continue
        # Original must be intact
        original = Path("contract.txt")
        if original.exists() and "Lin Park" not in original.read_text(encoding="utf-8"):
            print("FAIL: original contract.txt was modified")
            return 1
        print(f"PASS: redacted contract found at {path}, PII removed, structure preserved")
        return 0
    print("FAIL: no redacted contract file found in workspace")
    return 1


if __name__ == "__main__":
    sys.exit(main())
