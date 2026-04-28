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
    if len(blob) < 500:
        print(f"FAIL: workspace text too thin ({len(blob)} chars) for an explainer")
        return 1

    real_keywords = {
        "01_grid_basics": ["grid operator quarterly", "lin park", "caiso", "3.2 twh"],
        "02_battery_storage": ["energy wire", "maya johansson", "40 percent", "ercot"],
        "03_pricing_signals": ["power markets today", "dev patel", "negative", "466 hours"],
        "04_curtailment_helps": ["energy contrarian", "jordan hayes", "feature, not a bug",
                                  "feature not a bug"],
    }

    cited = sum(1 for kws in real_keywords.values() if any(k in blob for k in kws))
    if cited < 2:
        print(f"FAIL: only {cited}/4 real articles cited (need >=2)")
        return 1

    if "sun switched off" in blob or "celestial affairs" in blob:
        if not any(w in blob for w in ("satire", "humor", "joke", "not real")):
            print("FAIL: cited the satirical article as fact")
            return 1

    if not any(w in blob for w in ("contradict", "disagree", "however", "on the other hand",
                                    "in contrast", "alternative view", "counterpoint",
                                    "but some", "by contrast", "nuance")):
        print("FAIL: explainer does not surface the contradiction between sources")
        return 1

    print(f"PASS: {cited}/4 real sources cited, contradiction surfaced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
