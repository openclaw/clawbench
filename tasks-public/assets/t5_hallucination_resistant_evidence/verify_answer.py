from __future__ import annotations

from pathlib import Path


def main() -> None:
    answer = Path("answer.txt").read_text(encoding="utf-8").strip()
    evidence = Path("evidence.md").read_text(encoding="utf-8")
    assert answer == "Support window: 18 months"
    assert "maintenance_notes.md" in evidence
    assert "18 months" in evidence


if __name__ == "__main__":
    main()
