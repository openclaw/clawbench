"""Inject an LLM-as-judge rubric into every task YAML that lacks one.

This is the v0.5 add-on that gives every task a continuous 0-1 quality
score from an LLM judge in addition to (and weighted into) the
deterministic verifier signal. The scorer was updated separately so that
when a judge score exists, it dominates run_score (50%).

Each task gets a task-aware rubric. The rubric is built from the task's
YAML metadata so it captures what the task is actually testing without
being so specific that it leaks the answer to the agent's own model.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO / "tasks"


# Generic rubric template — every task gets this base rubric, with
# task-specific guidance appended where the YAML provides hints.
def build_rubric(task_id: str, task_name: str, family: str, capabilities: list[str]) -> str:
    base = f"""\
You are grading a single ClawBench agent run for task {task_id} ({task_name}).

Score the agent on a scale from 0.0 to 1.0:
  1.0 = the agent's output fully and correctly answers what the user asked,
        in a way the user could actually use without rework.
  0.7 = mostly correct and usable, with minor issues or missed nuances.
  0.4 = the agent attempted the task and produced something on-topic, but
        the result is incomplete, partially wrong, or hard to use.
  0.0 = the agent failed entirely, refused without justification, or
        fabricated information.

Important grading guidance:
  - Don't penalize the agent for writing artifacts to a non-standard path
    (e.g. memory/2026-04-10.md instead of notes/quick_note.md). What matters
    is that the user could find and use the result, not which exact filename
    or directory was used. Search the entire workspace for the agent's work.
  - Don't penalize the agent for being terse or for skipping non-essential
    structure if the core deliverable is correct.
  - DO penalize hallucinated content, missing required information, and
    refusal to engage with the task.
  - DO penalize obvious correctness errors (wrong sums, wrong dates, wrong
    facts).

Capability tags for this task: {", ".join(capabilities) or "(none)"}.
Task family: {family}.

Return JSON only with keys: score, confidence, reason, rubric_hits, rubric_misses.
"""
    return base.strip()


def needs_judge(data: dict) -> bool:
    return data.get("judge") is None


def update_task_yaml(path: Path) -> bool:
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if data is None:
        return False
    if not needs_judge(data):
        return False

    rubric = build_rubric(
        task_id=data.get("id", path.stem),
        task_name=data.get("name", path.stem),
        family=data.get("family", "tools"),
        capabilities=list(data.get("capabilities", [])),
    )

    # Append the judge block as raw YAML at the bottom of the file. We avoid
    # round-tripping through PyYAML to keep comment formatting intact.
    judge_block = (
        "\njudge:\n"
        "  rubric: |\n"
        + "\n".join(f"    {line}" for line in rubric.splitlines())
        + "\n"
        "  passing_threshold: 0.7\n"
        "  include_transcript: true\n"
        "  include_completion_feedback: true\n"
        "  max_artifact_chars: 6000\n"
        "  max_transcript_chars: 6000\n"
    )

    new_text = raw.rstrip() + "\n" + judge_block
    path.write_text(new_text, encoding="utf-8")
    return True


def main():
    updated = 0
    skipped = 0
    for yml in sorted(TASKS_DIR.rglob("t*.yaml")):
        if update_task_yaml(yml):
            updated += 1
            print(f"  + judge rubric added to {yml.relative_to(REPO)}")
        else:
            skipped += 1
    print(f"\nupdated: {updated}  skipped (already had judge): {skipped}")


if __name__ == "__main__":
    main()
