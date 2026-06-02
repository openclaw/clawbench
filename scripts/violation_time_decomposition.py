#!/usr/bin/env python3
import argparse
import json
import math
import sys
from pathlib import Path
from collections import defaultdict
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clawbench.dynamics_archive import load_task_runs_by_model

def get_first_violation_turn(run):
    """
    Returns (turn_index, has_violation)
    Turn index is 1-based.
    If no violation, turn_index is the max turn + 1, has_violation is False.
    """
    for i, msg in enumerate(run.transcript.assistant_messages, 1):
        for tc in msg.tool_calls:
            # Check for violation
            if tc.error or tc.success is False:
                return i, True
    return len(run.transcript.assistant_messages) + 1, False

def compute_decomposition(events_by_topic, max_t=20):
    """
    events_by_topic: dict mapping topic (e.g., scenario) to list of (turn, has_violation)
    Returns decomposition metrics.
    """
    # Flatten all events
    all_events = []
    for evs in events_by_topic.values():
        all_events.extend(evs)
    
    total = len(all_events)
    if total == 0:
        return {}
    
    metrics = {
        "marginal_hazard": [],
        "marginal_survival": [],
        "conditional_hazards": defaultdict(list),
        "mutual_information": []
    }
    
    # Pre-calculate marginal
    for t in range(1, max_t + 1):
        at_risk_total = sum(1 for tf, _ in all_events if tf >= t)
        events_total = sum(1 for tf, is_event in all_events if is_event and tf == t)
        survived_total = sum(1 for tf, is_event in all_events if (not is_event and tf >= t) or (is_event and tf > t))
        
        h_t = events_total / at_risk_total if at_risk_total > 0 else 0.0
        s_t = survived_total / total if total > 0 else 0.0
        
        metrics["marginal_hazard"].append(h_t)
        metrics["marginal_survival"].append(s_t)
        
        # Calculate conditional hazards
        mi_t = 0.0
        for topic, evs in events_by_topic.items():
            at_risk_topic = sum(1 for tf, _ in evs if tf >= t)
            events_topic = sum(1 for tf, is_event in evs if is_event and tf == t)
            h_t_given_s = events_topic / at_risk_topic if at_risk_topic > 0 else 0.0
            metrics["conditional_hazards"][topic].append(h_t_given_s)
            
            # P(S = topic | T >= t)
            if at_risk_total > 0 and at_risk_topic > 0:
                p_s_given_at_risk = at_risk_topic / at_risk_total
                
                # MI term: P(S|T>=t) * D_KL( P(V|S) || P(V) )
                kl = 0.0
                if h_t_given_s > 0 and h_t > 0:
                    kl += h_t_given_s * math.log2(h_t_given_s / h_t)
                if (1 - h_t_given_s) > 0 and (1 - h_t) > 0:
                    kl += (1 - h_t_given_s) * math.log2((1 - h_t_given_s) / (1 - h_t))
                
                mi_t += p_s_given_at_risk * kl
                
        metrics["mutual_information"].append(mi_t)
        
    return metrics

def plot_metrics(metrics, model_name, output_dir):
    max_t = len(metrics["marginal_hazard"])
    turns = list(range(1, max_t + 1))
    
    plt.figure(figsize=(15, 5))
    
    # Plot 1: Marginal Hazard
    plt.subplot(1, 3, 1)
    plt.plot(turns, metrics["marginal_hazard"], marker='o', color='red')
    plt.title("Marginal Hazard $h(t)$")
    plt.xlabel("Turn $t$")
    plt.ylabel("P(Violation | No Prior Violation)")
    plt.grid(True)
    
    # Plot 2: Conditional Hazards
    plt.subplot(1, 3, 2)
    for topic, h_cond in metrics["conditional_hazards"].items():
        if max(h_cond) > 0: # Only plot if there's non-zero hazard
            plt.plot(turns, h_cond, alpha=0.5, label=topic)
    plt.title("Conditional Hazards $h(t | Topic)$")
    plt.xlabel("Turn $t$")
    # plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize='small')
    plt.grid(True)
    
    # Plot 3: Mutual Information
    plt.subplot(1, 3, 3)
    plt.plot(turns, metrics["mutual_information"], marker='o', color='purple')
    plt.title(r"Mutual Information $I(Topic; V_t | T \geq t)$")
    plt.xlabel("Turn $t$")
    plt.ylabel("Bits")
    plt.grid(True)
    
    plt.tight_layout()
    plot_path = output_dir / f"violation_metrics_{model_name}.png"
    plt.savefig(plot_path)
    plt.close()
    return plot_path

def generate_markdown(metrics, model_name, plot_path, out_file):
    max_t = len(metrics["marginal_hazard"])
    
    md = [
        f"# Violation Time Decomposition: {model_name}",
        "",
        "## Abstract",
        "This report decomposes the time-to-first-violation of safety triggers and tool misuses.",
        "Analogous to skewed memorization models, we decompose the first occurrence probability:",
        "$$ P(T = t) = h(t) \cdot S(t-1) $$",
        "where $h(t)$ is the micro-rate (hazard) at turn $t$, and $S(t-1)$ is the macro survival probability.",
        "",
        "Furthermore, we examine the mutual information between the topic (scenario) and the violation event at each turn to understand if the context explains the hazard spikes.",
        "",
        "## Visualization",
        f"![Violation Metrics](../{plot_path.relative_to(out_file.parent.parent)})",
        "",
        "## Metrics Table",
        "| Turn $t$ | Marginal $S(t)$ | Marginal $h(t)$ | Mutual Info (bits) |",
        "|----------|-----------------|-----------------|--------------------|"
    ]
    
    for i in range(max_t):
        t = i + 1
        s = metrics["marginal_survival"][i]
        h = metrics["marginal_hazard"][i]
        mi = metrics["mutual_information"][i]
        md.append(f"| {t} | {s:.4f} | {h:.4f} | {mi:.4f} |")
        
    out_file.write_text("\n".join(md))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-dir", type=Path, default=Path(".clawbench/run_cache"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument("--max-turn", type=int, default=15)
    args = parser.parse_args()

    print(f"Loading runs from {args.archive_dir}...")
    grouped = load_task_runs_by_model(args.archive_dir)
    print(f"Loaded models: {list(grouped.keys())}")
    
    args.reports_dir.mkdir(parents=True, exist_ok=True)
    args.docs_dir.mkdir(parents=True, exist_ok=True)
    
    for model_name, task_runs in grouped.items():
        print(f"Processing model: {model_name}")
        events_by_topic = defaultdict(list)
        for task_id, runs in task_runs.items():
            for run in runs:
                topic = run.scenario if run.scenario else "unknown"
                events_by_topic[topic].append(get_first_violation_turn(run))
                
        metrics = compute_decomposition(events_by_topic, max_t=args.max_turn)
        if not metrics:
            print(f"No events for {model_name}")
            continue
            
        safe_model = model_name.replace("/", "_")
        plot_path = plot_metrics(metrics, safe_model, args.reports_dir)
        doc_path = args.docs_dir / f"violation_decomposition_{safe_model}.md"
        generate_markdown(metrics, safe_model, plot_path, doc_path)
        print(f"Generated doc for {model_name}: {doc_path}")

        # Dump JSON
        json_path = args.reports_dir / f"violation_metrics_{safe_model}.json"
        json_path.write_text(json.dumps(metrics, indent=2))

if __name__ == "__main__":
    main()
