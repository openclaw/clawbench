import json
import argparse
import shutil
from pathlib import Path

TEMPLATE = """# Semantic Space-Time Dynamics Report

## 1. Environment & Run Identity
- **Evaluated Model(s)**: {models}
- **Benchmark Version**: `{benchmark_version}`
- **Environment Checksum**: `{environment_checksum}`
- **Trajectory Representation**: `{embedding_model}`

## 2. Semantic-Temporal Metrics Summary

This table fuses the spatial reweighting metrics (Score) with long-term temporal trajectory bounds (Constraint Index & Information Loss).

| Task ID | Performance Score | Constraint Index ($C_q$) | Lagrangian Bound ($H_b$) | Participation Ratio ($PR$) |
|---|---|---|---|---|
{metrics_table}

## 3. Dynamics Insights
- **Constraint Index ($C_q$)**: Higher values indicate that the environment topology naturally restricts the agent's action manifold, making the trajectory more predictable over time.
- **Lagrangian Information Loss Bound**: Quantifies the upper bound on structural state-loss due to discrete token actions.
"""

def main():
    parser = argparse.ArgumentParser(description="Generate Space-Time Report")
    parser.add_argument("--eval-json", type=Path, default=Path("results/gpt_oss_eval.json"))
    parser.add_argument("--constraint-json", type=Path, default=Path("results/posterior_reports/constraint_index.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/space_time_report"))
    parser.add_argument("--embedding-model", type=str, default="bag-of-words", help="The embedding model used for spatial trajectory representation")
    args = parser.parse_args()

    # Read base eval JSON
    if args.eval_json.exists():
        with open(args.eval_json, "r") as f:
            eval_data = json.load(f)
    else:
        eval_data = {"model": "Unknown", "benchmark_version": "N/A", "environment_checksum": "N/A", "task_results": []}

    # Read Constraint Index JSON
    if args.constraint_json.exists():
        with open(args.constraint_json, "r") as f:
            constraint_data = json.load(f)
    else:
        constraint_data = {}

    # Build Table
    table_rows = []
    task_scores = {t["task_id"]: t["mean_task_score"] for t in eval_data.get("task_results", [])}

    # Merge tasks from both
    all_tasks = set(task_scores.keys()).union(set(constraint_data.keys()))

    for task_id in sorted(all_tasks):
        score = task_scores.get(task_id, 0.0)
        c_q = constraint_data.get(task_id, {}).get("C_q", 0.0)
        lagrangian = constraint_data.get(task_id, {}).get("lagrangian_info_loss_bound", 0.0)
        pr = constraint_data.get(task_id, {}).get("PR", 0.0)

        row = f"| `{task_id}` | {score:.3f} | {c_q:.3f} | {lagrangian:.3f} | {pr:.3f} |"
        table_rows.append(row)

    metrics_table = "\n".join(table_rows)

    report_content = TEMPLATE.format(
        models=eval_data.get("model", "Unknown"),
        benchmark_version=eval_data.get("benchmark_version", "N/A"),
        environment_checksum=eval_data.get("environment_checksum", "N/A"),
        embedding_model=args.embedding_model,
        metrics_table=metrics_table
    )

    # Automatically link visualizations from dynamics output directories
    # and copy them cleanly into a plots/ subfolder so everything is self-contained.
    results_dir = args.output_dir.parent
    plots_dir = args.output_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    vis_content = "\n## 4. Spatio-Temporal Visualizations\n\n"
    has_vis = False

    important_plots = [
        ("PCA Trajectories by Tier", "pca_by_tier.png"),
        ("Pairwise Contraction & Divergence", "pairwise_contraction_scatter.png"),
        ("Prompt Perturbation Sensitivity Heatmap", "sensitivity_heatmap.png"),
        ("Task Completion Survival Curve", "survival_first_correct_write.png")
    ]

    for dyn_dir in sorted(results_dir.glob("*_dynamics")):
        if dyn_dir.is_dir():
            model_name = dyn_dir.name.replace("_eval_dynamics", "").replace("_", " ").title()
            vis_content += f"### {model_name}\n\n"
            for title, filename in important_plots:
                plot_file = dyn_dir / filename
                if plot_file.exists():
                    dest_name = f"{model_name.replace(' ', '_').lower()}_{filename}"
                    dest_file = plots_dir / dest_name
                    shutil.copy2(plot_file, dest_file)

                    # Use relative paths for markdown links within the self-contained folder
                    vis_content += f"**{title}**\n\n![{title}](plots/{dest_name})\n\n"
                    has_vis = True

    if has_vis:
        report_content += vis_content

    # Check for degenerate single-step trajectories and add a note
    degenerate_note = ""
    for dyn_dir in sorted(results_dir.glob("*_dynamics")):
        dyn_json = dyn_dir / "dynamics.json"
        if dyn_json.exists():
            try:
                dyn_data = json.load(open(dyn_json))
                per_run = dyn_data.get("per_run", [])
                if per_run:
                    max_steps = max(r.get("n_steps", 0) for r in per_run)
                    if max_steps <= 1:
                        degenerate_note = """
## 5. Trajectory Validity Note

> **⚠️ Single-Step Trajectories Detected**
>
> All runs in this evaluation completed in a single agent turn (`n_steps=1`).
> This means the PCA trajectory plots, survival curves, and regime classifications
> are **degenerate** — there is no multi-step temporal evolution to analyze.
>
> **This is expected for local dev runs** using small models (e.g., Ollama 20B/27B)
> on simple Tier 1 tasks. These models emit a single response and terminate,
> producing no iterative reasoning loop.
>
> To produce meaningful spatio-temporal dynamics, the evaluation requires:
> - **Multi-turn tasks** (Tier 3+) that demand iterative tool use, debugging, and self-correction
> - **Capable models** (70B+ or frontier API models) that engage in multi-step agentic reasoning
> - **Extended compute budgets** to support 10-50+ turn trajectories per task
>
> The constraint index ($C_q$) and inter-run predictability (BOPS) metrics in the table above
> remain valid, as they operate across repeated runs rather than within a single trajectory.

"""
                        break
            except (json.JSONDecodeError, KeyError):
                pass

    if degenerate_note:
        report_content += degenerate_note

    # Computational requirements section
    report_content += """
## 6. Computational Requirements for Full Dynamics

Spatio-temporal dynamics analysis is fundamentally a **high-compute evaluation methodology**.
Unlike single-pass benchmarks, it requires:

| Requirement | Why |
|-------------|-----|
| **Multiple runs per task** (≥3) | Inter-run variance estimation for BOPS and constraint index |
| **Multi-step trajectories** (10-50+ turns) | PCA embedding, regime classification, survival analysis |
| **Perturbed task variants** | Lyapunov sensitivity estimation ($\\hat{\\lambda}$) |
| **Dense semantic embeddings** | Kernelized entropy estimation in high-dimensional trajectory space |

A full production evaluation with 2 frontier models × 50 tasks × 3 runs × 30 avg turns
requires approximately **9,000 agent turns** — orders of magnitude more compute than a
standard single-pass benchmark, but necessary to characterize the operational stability
of agents deployed in long-horizon autonomous settings.
"""

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_md = args.output_dir / "EVAL_REPORT_SPACE_TIME.md"
    with open(output_md, "w") as f:
        f.write(report_content)

    print(f"Generated Space-Time Report at: {output_md}")

if __name__ == "__main__":
    main()
