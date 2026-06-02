#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path

import yaml


DEFAULT_TASK_IDS = [
    "t1-bugfix-discount",
    "t1-fs-quick-note",
    "t2-browser-form-fix",
]


def _clean_paraphrase(text: str) -> str:
    """Keep final-only output from local models that expose reasoning traces."""
    marker = "...done thinking."
    if marker in text:
        text = text.rsplit(marker, 1)[-1]
    return text.strip()


def _find_task_file(base_dir: Path, task_id: str) -> Path:
    matches = sorted(base_dir.glob(f"tier*/{task_id}.yaml"))
    if not matches:
        raise FileNotFoundError(f"No task YAML found for id: {task_id}")
    if len(matches) > 1:
        raise ValueError(f"Multiple task YAML files found for id {task_id}: {matches}")
    return matches[0]


def generate_paraphrase(text: str, model="qwen3.5:27b") -> str:
    """Use local Ollama to generate a semantic paraphrase."""
    prompt = (
        "Paraphrase the following task instruction. "
        "Keep the exact same semantic meaning and intent, but change the wording slightly. "
        "Output ONLY the paraphrased text, nothing else.\n\n"
        f"Original: {text}"
    )

    cmd = ["ollama", "run", model, prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        paraphrase = _clean_paraphrase(result.stdout)
        return paraphrase or text
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"Error running ollama: {e}")
        return text


def main():
    parser = argparse.ArgumentParser(description="Generate deterministic perturbed task variants.")
    parser.add_argument("--base-dir", type=Path, default=Path("tasks-public"))
    parser.add_argument("--model", default="qwen3.5:27b")
    parser.add_argument(
        "--task",
        action="append",
        dest="task_ids",
        help="Task id to perturb. May be passed multiple times.",
    )
    args = parser.parse_args()

    task_ids = args.task_ids or DEFAULT_TASK_IDS
    selected_tasks = [_find_task_file(args.base_dir, task_id) for task_id in task_ids]

    for file_path in selected_tasks:
        print(f"Processing {file_path}...")
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Modify ID and Name
        original_id = data["id"]
        data["id"] = f"{original_id}-perturbed"
        data["name"] = data["name"] + " (Perturbed)"
        rubric = data.get("judge", {}).get("rubric")
        if isinstance(rubric, str):
            data["judge"]["rubric"] = rubric.replace(original_id, data["id"])

        # Paraphrase the user prompt
        if "user" in data and "turns" in data["user"]:
            for turn in data["user"]["turns"]:
                original_text = turn["message"]
                print(f"  Original: {original_text}")
                paraphrased_text = generate_paraphrase(original_text, model=args.model)
                print(f"  Paraphrased: {paraphrased_text}")
                turn["message"] = paraphrased_text

        # Write to new file
        new_path = file_path.with_name(f"{file_path.stem}-perturbed.yaml")
        with open(new_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, sort_keys=False, default_flow_style=False)
        print(f"  Wrote {new_path}")


if __name__ == "__main__":
    main()
