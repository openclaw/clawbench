"""Plotting utilities for dynamics analysis.

Generates publication-ready figures from dynamics data and saves to a
results directory. All plots use matplotlib with the Agg backend so they
work headlessly.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from clawbench.dynamics import (
    Dynamics,
    StratifiedAssessment,
    StratumStats,
    SurvivalPoint,
)


def _savefig(fig: plt.Figure, path: Path) -> None:
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _plot_series_curves(
    dynamics_list: list[Dynamics],
    labels: list[str],
    out_path: Path,
    *,
    series_attr: str,
    ylabel: str,
    title: str,
) -> None:
    """Plot a step-aligned per-run series coloured by label."""
    fig, ax = plt.subplots(figsize=(10, 5))
    cmap = plt.cm.tab10
    unique = sorted(set(labels))
    colour_map = {lbl: cmap(i / max(len(unique) - 1, 1)) for i, lbl in enumerate(unique)}

    for d, lbl in zip(dynamics_list, labels):
        series = np.asarray(getattr(d, series_attr), dtype=float)
        if len(series) < 2:
            continue
        ax.plot(series, alpha=0.6, color=colour_map[lbl], linewidth=1)

    for lbl in unique:
        ax.plot([], [], color=colour_map[lbl], label=lbl, linewidth=2)
    ax.legend(fontsize=8, loc="upper left")
    ax.set_xlabel("Step")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    _savefig(fig, out_path)


def plot_drift_curves(
    dynamics_list: list[Dynamics],
    labels: list[str],
    out_path: Path,
) -> None:
    """Drift-from-origin curves coloured by label (e.g. task_id or regime)."""
    _plot_series_curves(
        dynamics_list,
        labels,
        out_path,
        series_attr="drift",
        ylabel="Cosine distance from step 0",
        title="Drift from Origin",
    )


def plot_step_size_curves(
    dynamics_list: list[Dynamics],
    labels: list[str],
    out_path: Path,
) -> None:
    """Step-to-step movement curves coloured by label."""
    _plot_series_curves(
        dynamics_list,
        labels,
        out_path,
        series_attr="step_size",
        ylabel="Cosine distance from previous step",
        title="Step-to-Step Movement",
    )


def plot_pca_trajectories(
    dynamics_list: list[Dynamics],
    labels: list[str],
    out_path: Path,
) -> None:
    """PCA phase portraits (PC1 vs PC2) coloured by label."""
    fig, ax = plt.subplots(figsize=(8, 8))
    cmap = plt.cm.tab10
    unique = sorted(set(labels))
    colour_map = {lbl: cmap(i / max(len(unique) - 1, 1)) for i, lbl in enumerate(unique)}

    for d, lbl in zip(dynamics_list, labels):
        if d.pca_trajectory is None or len(d.pca_trajectory) < 2:
            continue
        traj = d.pca_trajectory
        ax.plot(traj[:, 0], traj[:, 1], alpha=0.5, color=colour_map[lbl], linewidth=1)
        ax.scatter(traj[0, 0], traj[0, 1], color=colour_map[lbl], marker="o", s=30, zorder=5)
        ax.scatter(traj[-1, 0], traj[-1, 1], color=colour_map[lbl], marker="x", s=30, zorder=5)

    for lbl in unique:
        ax.plot([], [], color=colour_map[lbl], label=lbl, linewidth=2)
    ax.legend(fontsize=8)
    ax.set_xlabel("PC1")
    ax.set_ylabel("PC2")
    ax.set_title("PCA Phase Portrait (o=start, x=end)")
    _savefig(fig, out_path)


def plot_regime_distribution(
    strata: list[StratumStats],
    stratifier_name: str,
    out_path: Path,
) -> None:
    """Stacked bar chart of regime counts per stratum."""
    fig, ax = plt.subplots(figsize=(10, 5))
    all_regimes = sorted({r for s in strata for r in s.regime_counts})
    x = np.arange(len(strata))
    bottom = np.zeros(len(strata))
    cmap = plt.cm.Set2

    for j, regime in enumerate(all_regimes):
        counts = [s.regime_counts.get(regime, 0) for s in strata]
        ax.bar(x, counts, bottom=bottom, label=regime, color=cmap(j / max(len(all_regimes) - 1, 1)))
        bottom += np.array(counts)

    ax.set_xticks(x)
    ax.set_xticklabels([s.name for s in strata], rotation=30, ha="right")
    ax.set_ylabel("Count")
    ax.set_title(f"Regime Distribution by {stratifier_name}")
    ax.legend(fontsize=8)
    _savefig(fig, out_path)


def plot_score_distributions(
    strata: list[StratumStats],
    stratifier_name: str,
    out_path: Path,
) -> None:
    """Box plots of score distributions per stratum."""
    fig, ax = plt.subplots(figsize=(10, 5))
    data = [s.scores for s in strata if len(s.scores) > 0]
    labels = [s.name for s in strata if len(s.scores) > 0]

    if data:
        ax.boxplot(data, labels=labels, patch_artist=True,
                   boxprops=dict(facecolor="lightblue", alpha=0.7))
    ax.set_ylabel("Score")
    ax.set_title(f"Score Distribution by {stratifier_name}")
    plt.xticks(rotation=30, ha="right")
    _savefig(fig, out_path)


def plot_survival_curve(
    km_points: list[SurvivalPoint],
    event_name: str,
    out_path: Path,
) -> None:
    """Kaplan-Meier survival curve."""
    if not km_points:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    times = [p.time for p in km_points]
    surv = [p.survival for p in km_points]
    ax.step(times, surv, where="post", linewidth=2, color="steelblue")
    ax.fill_between(times, surv, step="post", alpha=0.15, color="steelblue")
    ax.set_xlabel("Step")
    ax.set_ylabel("Survival probability")
    ax.set_title(f"Kaplan-Meier: {event_name}")
    ax.set_ylim(-0.05, 1.05)
    _savefig(fig, out_path)


def plot_stratum_dynamics_heatmap(
    strata: list[StratumStats],
    stratifier_name: str,
    out_path: Path,
) -> None:
    """Heatmap of mean dynamics metrics across strata."""
    metrics = ["entropy", "error_rate", "constraint", "memory_depth", "mean_drift", "mean_step_size"]
    data = np.zeros((len(strata), len(metrics)))
    for i, s in enumerate(strata):
        arrays = [s.entropy_dist, s.error_rate_dist, s.constraint_dist,
                  s.memory_depth_dist, s.mean_drift_dist, s.mean_step_size_dist]
        for j, arr in enumerate(arrays):
            data[i, j] = float(np.mean(arr)) if len(arr) > 0 else 0.0

    fig, ax = plt.subplots(figsize=(10, max(3, len(strata) * 0.6)))
    im = ax.imshow(data, aspect="auto", cmap="YlOrRd")
    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels(metrics, rotation=30, ha="right")
    ax.set_yticks(range(len(strata)))
    ax.set_yticklabels([s.name for s in strata])
    for i in range(len(strata)):
        for j in range(len(metrics)):
            ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax.set_title(f"Dynamics Metrics by {stratifier_name}")
    _savefig(fig, out_path)


def plot_pairwise_divergence_curves(
    per_task_sensitivity: dict[str, dict],
    out_path: Path,
) -> bool:
    """Plot mean pairwise trajectory divergence over aligned steps."""
    if not per_task_sensitivity:
        return False

    fig, ax = plt.subplots(figsize=(10, 5))
    cmap = plt.cm.tab10
    tasks = sorted(per_task_sensitivity)
    colour_map = {task: cmap(i / max(len(tasks) - 1, 1)) for i, task in enumerate(tasks)}

    plotted = False
    for task in tasks:
        summary = per_task_sensitivity[task]
        mean_curve = np.asarray(summary.get("mean_divergence_curve", []), dtype=float)
        std_curve = np.asarray(summary.get("std_divergence_curve", []), dtype=float)
        if len(mean_curve) == 0:
            continue
        steps = np.arange(len(mean_curve))
        ax.plot(steps, mean_curve, linewidth=2, color=colour_map[task], label=task)
        if len(std_curve) == len(mean_curve):
            ax.fill_between(steps, mean_curve - std_curve, mean_curve + std_curve, color=colour_map[task], alpha=0.12)
        plotted = True

    if not plotted:
        plt.close(fig)
        return False

    ax.set_xlabel("Aligned step")
    ax.set_ylabel("Pairwise embedding divergence")
    ax.set_title("Do Repeated Trajectories Converge or Diverge?")
    ax.legend(fontsize=8)
    _savefig(fig, out_path)
    return True


def plot_pairwise_contraction_scatter(
    per_task_sensitivity: dict[str, dict],
    out_path: Path,
) -> bool:
    """Scatter initial vs final pairwise divergence; below diagonal means convergence."""
    if not per_task_sensitivity:
        return False

    fig, ax = plt.subplots(figsize=(7, 6))
    cmap = plt.cm.tab10
    tasks = sorted(per_task_sensitivity)
    colour_map = {task: cmap(i / max(len(tasks) - 1, 1)) for i, task in enumerate(tasks)}

    max_seen = 0.0
    plotted = False
    for task in tasks:
        points = per_task_sensitivity[task].get("pair_points", [])
        if not points:
            continue
        xs = [point["initial_divergence"] for point in points]
        ys = [point["final_divergence"] for point in points]
        max_seen = max(max_seen, *(xs + ys))
        ax.scatter(xs, ys, s=60, alpha=0.8, color=colour_map[task], label=task)
        plotted = True

    if not plotted:
        plt.close(fig)
        return False

    limit = max(max_seen, 0.1)
    ax.plot([0, limit], [0, limit], linestyle="--", color="black", linewidth=1)
    ax.set_xlabel("Initial pairwise divergence")
    ax.set_ylabel("Final pairwise divergence")
    ax.set_title("Pairwise Trajectory Contraction")
    ax.legend(fontsize=8)
    _savefig(fig, out_path)
    return True


def plot_sensitivity_heatmap(
    per_task_sensitivity: dict[str, dict],
    out_path: Path,
) -> bool:
    """Heatmap of per-task sensitivity metrics."""
    if not per_task_sensitivity:
        return False

    metrics = [
        ("mean_score_delta", "score_delta"),
        ("mean_tool_edit_distance", "tool_edit"),
        ("mean_family_js_divergence", "js_div"),
        ("mean_lyapunov_proxy", "lyapunov"),
        ("fraction_converging_pairs", "frac_converging"),
    ]
    tasks = sorted(per_task_sensitivity)
    data = np.zeros((len(tasks), len(metrics)))
    for row_idx, task in enumerate(tasks):
        summary = per_task_sensitivity[task]
        for col_idx, (key, _label) in enumerate(metrics):
            data[row_idx, col_idx] = float(summary.get(key, 0.0))

    fig, ax = plt.subplots(figsize=(9, max(3, len(tasks) * 0.7)))
    im = ax.imshow(data, aspect="auto", cmap="Blues")
    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels([label for _key, label in metrics], rotation=30, ha="right")
    ax.set_yticks(range(len(tasks)))
    ax.set_yticklabels(tasks)
    for row_idx in range(len(tasks)):
        for col_idx in range(len(metrics)):
            ax.text(col_idx, row_idx, f"{data[row_idx, col_idx]:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax.set_title("Pairwise Sensitivity by Task")
    _savefig(fig, out_path)
    return True


def generate_all_plots(
    dynamics_list: list[Dynamics],
    runs: list,
    stratified: dict[str, StratifiedAssessment],
    km_points: list[SurvivalPoint] | None = None,
    event_name: str = "first_correct_write",
    out_dir: Path = Path("results"),
    sensitivity_summary: dict[str, dict] | None = None,
) -> list[Path]:
    """Generate all dynamics plots and return list of saved paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    # Labels by regime
    regime_labels = [d.regime.value for d in dynamics_list]
    tier_labels = []
    for r in runs:
        tid = r.task_id.lower()
        tier = "unknown"
        for i in range(1, 6):
            if tid.startswith(f"t{i}_") or tid.startswith(f"t{i}-"):
                tier = f"tier{i}"
                break
        tier_labels.append(tier)

    # Drift curves by regime
    p = out_dir / "drift_by_regime.png"
    plot_drift_curves(dynamics_list, regime_labels, p)
    saved.append(p)

    # Drift curves by tier
    p = out_dir / "drift_by_tier.png"
    plot_drift_curves(dynamics_list, tier_labels, p)
    saved.append(p)

    p = out_dir / "step_size_by_regime.png"
    plot_step_size_curves(dynamics_list, regime_labels, p)
    saved.append(p)

    p = out_dir / "step_size_by_tier.png"
    plot_step_size_curves(dynamics_list, tier_labels, p)
    saved.append(p)

    # PCA trajectories
    has_pca = any(d.pca_trajectory is not None for d in dynamics_list)
    if has_pca:
        p = out_dir / "pca_by_regime.png"
        plot_pca_trajectories(dynamics_list, regime_labels, p)
        saved.append(p)
        p = out_dir / "pca_by_tier.png"
        plot_pca_trajectories(dynamics_list, tier_labels, p)
        saved.append(p)

    # Per-stratifier plots
    for name, sa in stratified.items():
        p = out_dir / f"regimes_by_{name}.png"
        plot_regime_distribution(sa.strata, name, p)
        saved.append(p)

        p = out_dir / f"scores_by_{name}.png"
        plot_score_distributions(sa.strata, name, p)
        saved.append(p)

        p = out_dir / f"dynamics_heatmap_{name}.png"
        plot_stratum_dynamics_heatmap(sa.strata, name, p)
        saved.append(p)

    # Survival curve
    if km_points:
        p = out_dir / f"survival_{event_name}.png"
        plot_survival_curve(km_points, event_name, p)
        saved.append(p)

    per_task_sensitivity = (sensitivity_summary or {}).get("same_task", {}).get("per_task", {})
    p = out_dir / "pairwise_divergence_by_task.png"
    if plot_pairwise_divergence_curves(per_task_sensitivity, p):
        saved.append(p)

    p = out_dir / "pairwise_contraction_scatter.png"
    if plot_pairwise_contraction_scatter(per_task_sensitivity, p):
        saved.append(p)

    p = out_dir / "sensitivity_heatmap.png"
    if plot_sensitivity_heatmap(per_task_sensitivity, p):
        saved.append(p)

    return saved
