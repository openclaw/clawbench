"""Merge cherry-pick rerun results into a final fair archive.

Layers:
  base:    data/run_cache_archive/v2026-4-19-full/<model>/<task>/runN.json
  overlay: data/run_cache_archive/v2026-4-20-cherry/<model>/<task>/runN.json
  output:  data/run_cache_archive/v2026-4-20-final/<model>/<task>/runN.json

For every (model, task, run_idx) in the union:
  - If the overlay has it, use overlay (it was re-run with fixes).
  - Otherwise use base (original run was already valid).

Usage:
    python3 scripts/merge_cherrypick.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "run_cache_archive" / "v2026-4-19-full"
OVERLAY = ROOT / "data" / "run_cache_archive" / "v2026-4-20-cherry"
OUT = ROOT / "data" / "run_cache_archive" / "v2026-4-20-final"


def collect(root: Path) -> dict[tuple[str, str, str], Path]:
    """Map (model, task, run_file) -> absolute path."""
    out = {}
    if not root.exists():
        return out
    for model_dir in root.iterdir():
        if not model_dir.is_dir():
            continue
        for task_dir in model_dir.iterdir():
            if not task_dir.is_dir():
                continue
            for rf in task_dir.glob("run*.json"):
                out[(model_dir.name, task_dir.name, rf.name)] = rf
    return out


def main() -> None:
    base = collect(BASE)
    overlay = collect(OVERLAY)
    print(f"Base   (v4-19-full): {len(base)} runs")
    print(f"Overlay (v4-20-cherry): {len(overlay)} runs")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    from_base = 0
    from_overlay = 0
    both_keys = set(base) | set(overlay)
    for key in sorted(both_keys):
        model, task, rf = key
        dst_dir = OUT / model / task
        dst_dir.mkdir(parents=True, exist_ok=True)
        src = overlay.get(key) or base.get(key)
        if key in overlay:
            from_overlay += 1
        else:
            from_base += 1
        shutil.copy2(src, dst_dir / rf)

    # Write a manifest
    manifest = {
        "base_tag": "v2026-4-19-full",
        "overlay_tag": "v2026-4-20-cherry",
        "output_tag": "v2026-4-20-final",
        "counts": {
            "total": len(both_keys),
            "from_base": from_base,
            "from_overlay": from_overlay,
        },
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print()
    print(f"Merged into: {OUT}")
    print(f"  from base:    {from_base}")
    print(f"  from overlay: {from_overlay}")
    print(f"  total:        {len(both_keys)}")

    # Per-model summary
    print()
    from collections import defaultdict
    by_model = defaultdict(lambda: {"base": 0, "overlay": 0})
    for key in both_keys:
        m = key[0]
        if key in overlay:
            by_model[m]["overlay"] += 1
        else:
            by_model[m]["base"] += 1
    for m in sorted(by_model):
        v = by_model[m]
        print(f"  {m:<40}  base={v['base']:>3}  overlay={v['overlay']:>3}  total={v['base']+v['overlay']}")


if __name__ == "__main__":
    main()
