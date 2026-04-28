from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BUGGY_EMOJI = """import re

EMOJI_RE = re.compile(r"[\\U0001F300-\\U0001FAFF]")


def normalize_title(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned.strip().title()


def normalize_tags(raw: str) -> list[str]:
    return [part.strip().lower() for part in raw.split(",") if part.strip()]
"""

BUGGY_TAGS = """import re

EMOJI_RE = re.compile(r"[\\U0001F300-\\U0001FAFF]")


def normalize_title(text: str) -> str:
    cleaned = " ".join(text.split())
    cleaned = EMOJI_RE.sub("", cleaned)
    return cleaned.strip().title()


def normalize_tags(raw: str) -> list[str]:
    return [part.strip().lower() for part in raw.split(",")]
"""


def _run_pytest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _expect_mutant_failure(normalizer_path: Path, mutant_source: str, label: str) -> None:
    backup = normalizer_path.read_text(encoding="utf-8")
    normalizer_path.write_text(mutant_source, encoding="utf-8")
    try:
        result = _run_pytest("tests/test_normalizer.py")
        assert result.returncode != 0, f"student tests did not catch mutant: {label}"
    finally:
        normalizer_path.write_text(backup, encoding="utf-8")


def main() -> None:
    test_path = Path("tests/test_normalizer.py")
    assert test_path.exists(), "tests/test_normalizer.py is missing"

    baseline = _run_pytest()
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    normalizer_path = Path("normalizer.py")
    _expect_mutant_failure(normalizer_path, BUGGY_EMOJI, "emoji stripping")
    _expect_mutant_failure(normalizer_path, BUGGY_TAGS, "blank tag handling")

    source = test_path.read_text(encoding="utf-8").lower()
    assert "normalize_title" in source
    assert "normalize_tags" in source


if __name__ == "__main__":
    main()
