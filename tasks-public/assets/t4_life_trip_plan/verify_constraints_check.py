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


import json

def main() -> int:
    places_path = Path("places.json")
    if not places_path.exists():
        print("FAIL: places.json missing")
        return 1
    places = json.loads(places_path.read_text(encoding="utf-8"))
    veg_venues = [v["name"].lower() for v in places["venues"] if v.get("vegetarian_friendly")]

    blob = workspace_blob().lower()

    # If wagyu mentioned, must be excluded
    if "wagyu" in blob:
        if not any(w in blob for w in ("not vegetarian", "skip", "exclude", "instead",
                                        "alternative", "won't include", "dietary",
                                        "won't be visit", "remov")):
            print("FAIL: wagyu_house mentioned but not excluded for dietary reasons")
            return 1

    # Must reference at least one veg venue
    if not any(name in blob for name in veg_venues):
        print("FAIL: itinerary doesn't include any vegetarian-friendly venue")
        return 1

    print("PASS: dietary constraint honored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
