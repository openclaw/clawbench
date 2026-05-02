"""Splice gap-fill runs into the v4-19-full archive.

For each model's missing (task, run_idx) slot, copy the corresponding runN.json
from v2026-4-20-gapfill archive into v2026-4-19-full.

Only fills MISSING slots — never overwrites existing archive entries.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "run_cache_archive" / "v2026-4-19-full"
SOURCE = ROOT / "data" / "run_cache_archive" / "v2026-4-20-gapfill-v2"

# Expected missing slots per model
MISSING = {
    "anthropic_claude-opus-4-7":         [("t5-contradictory-requirements", 0)],
    "anthropic_claude-opus-4-6":         [("t1-cal-quick-reminder", 2)],
    "anthropic_claude-sonnet-4-6":       [
        ("t2-ctx-pronoun-resolve", 0), ("t2-ctx-pronoun-resolve", 1),
        ("t4-memory-recall-continuation", 1), ("t4-memory-recall-continuation", 2),
    ],
    "openai_gpt-5.4":                    [
        ("t2-browser-form-fix", 1), ("t3-debug-timezone-regression", 0),
        ("t4-browser-research-and-code", 0),
    ],
    "google_gemini-3.1-pro-preview":     [
        ("t2-ctx-pronoun-resolve", 0), ("t2-ctx-pronoun-resolve", 1),
    ],
    "openrouter_z-ai_glm-5.1":           [
        ("t2-sys-memory-roundtrip", 2), ("t2-web-quick-fact", 2),
        ("t4-memory-recall-continuation", 0),
    ],
    "openrouter_minimax_minimax-m2.7":   [
        ("t1-bugfix-discount", 2), ("t5-contradictory-requirements", 2),
    ],
    "openrouter_moonshotai_kimi-k2.5":   [("t4-life-trip-plan", 1)],
    "openrouter_qwen_qwen3.6-plus":      [
        ("t2-ctx-pronoun-resolve", 0), ("t2-ctx-pronoun-resolve", 1),
        ("t2-ctx-pronoun-resolve", 2),
    ],
}


def main() -> None:
    total = sum(len(v) for v in MISSING.values())
    print(f"Splicing {total} missing runs...")
    spliced = 0
    skipped = 0
    failed = []

    # Track which source runs have been consumed per (sub, task) — avoid
    # splicing the SAME gap-fill run into multiple destination slots.
    consumed: dict[tuple[str, str], set[int]] = {}

    for sub, slots in MISSING.items():
        for task, run_idx in slots:
            dst = TARGET / sub / task / f"run{run_idx}.json"
            if dst.exists():
                print(f"  SKIP (already present): {sub}/{task}/run{run_idx}")
                skipped += 1
                continue

            src_dir = SOURCE / sub / task
            if not src_dir.exists():
                failed.append(f"{sub}/{task}/run{run_idx}: source dir missing")
                continue

            # Pick the lowest-indexed unconsumed source run
            used = consumed.setdefault((sub, task), set())
            src = None
            for i in range(3):
                if i in used:
                    continue
                cand = src_dir / f"run{i}.json"
                if cand.exists():
                    src = cand
                    used.add(i)
                    break
            if src is None:
                failed.append(f"{sub}/{task}/run{run_idx}: no unconsumed source run available")
                continue

            # Validate the source run is reasonable (has a transcript)
            try:
                d = json.loads(src.read_text())
                if not d.get("transcript", {}).get("messages"):
                    failed.append(f"{sub}/{task}/run{run_idx}: source empty transcript")
                    continue
            except Exception as e:
                failed.append(f"{sub}/{task}/run{run_idx}: load failed {e}")
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            # Rewrite the file so internal 'run_index' in metadata (if any) matches.
            # Most run.json files don't embed run_index so we leave content as-is.
            print(f"  OK   {sub}/{task}/run{run_idx}  <=  {src.name}")
            spliced += 1

    print()
    print(f"Spliced: {spliced}")
    print(f"Skipped (already present): {skipped}")
    print(f"Failed: {len(failed)}")
    for f in failed:
        print(f"  ! {f}")


if __name__ == "__main__":
    main()
