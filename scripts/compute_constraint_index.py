#!/usr/bin/env python3
"""Compute posterior Constraint Index C(q) from cached runs.

Task-level constraint index:

    C(q) = -z(PR(q)) - z(H(q)) + z(BOPS(q))

Where:

    PR(q)   = participation ratio of the task response covariance
    H(q)    = Shannon entropy of the covariance eigenspectrum
    BOPS(q) = within-model inter-run predictability proxy

High C(q) means a task is more constrained: models and repeated runs tend to
land in a narrower response manifold. Low C(q) means the task is more open or
stylistically underconstrained.

This implementation uses a normalized bag-of-words representation built from
the full assistant trajectory text plus tool-call names and compacted inputs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawbench.dynamics_archive import load_task_runs_by_model

WORD_RE = re.compile(r"[a-z]{3,}")
STOPWORDS = set(
    "the and that with this have from what your will can but not "
    "was are been one would there they their has had its were only some "
    "than about these which into also each when where them how who very "
    "much more most other then here such does like just make many want need take".split()
)


def _assistant_trajectory_text(run, max_chars: int = 4000) -> str:
    parts = []
    for message in run.transcript.assistant_messages:
        if message.text:
            parts.append(message.text)
        for call in message.tool_calls:
            parts.append(call.name)
            if call.input:
                parts.append(json.dumps(call.input, sort_keys=True)[:200])
    return " ".join(p for p in parts if p).strip()[:max_chars]


def _fallback_text_from_any_message(run) -> str:
    for msg in reversed(run.transcript.messages):
        parts = []
        if msg.text:
            parts.append(msg.text)
        for call in msg.tool_calls:
            parts.append(call.name)
            if call.input:
                parts.append(json.dumps(call.input, sort_keys=True)[:200])
        if parts:
            return " ".join(parts).strip()
    return ""


def tokenize(text: str) -> list[str]:
    return [w for w in WORD_RE.findall((text or "").lower()) if w not in STOPWORDS]


def build_vocab(texts: list[str], top_k: int = 500) -> dict[str, int]:
    counts = Counter()
    for text in texts:
        counts.update(set(tokenize(text)))
    return {word: idx for idx, (word, _) in enumerate(counts.most_common(top_k))}


def vectorize(text: str, vocab: dict[str, int]) -> np.ndarray:
    vec = np.zeros(len(vocab), dtype=np.float32)
    toks = tokenize(text)
    if not toks:
        return vec
    counts = Counter(toks)
    for word, cnt in counts.items():
        if word in vocab:
            vec[vocab[word]] = cnt
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def participation_ratio(X: np.ndarray) -> float:
    """PR(X) = (tr Sigma)^2 / tr(Sigma^2), an effective dimensionality proxy."""
    if X.shape[0] < 2:
        return 1.0
    sigma = np.cov(X.T)
    if sigma.ndim == 0:
        return 1.0
    tr = np.trace(sigma)
    tr_sq = np.trace(sigma @ sigma)
    if tr_sq < 1e-12:
        return 1.0
    return float((tr**2) / tr_sq)


def response_entropy(X: np.ndarray) -> float:
    """Entropy over normalized covariance eigenvalues, in bits."""
    if X.shape[0] < 2:
        return 0.0
    sigma = np.cov(X.T)
    eigs = np.linalg.eigvalsh(sigma)
    eigs = np.clip(eigs, 1e-12, None)
    probs = eigs / eigs.sum()
    return float(-np.sum(probs * np.log2(probs)))


def bops_inter_run_predictability(run_vecs: dict[str, list[np.ndarray]]) -> float:
    """Mean within-model pairwise cosine similarity across repeated runs."""
    per_model_means = []
    for vecs in run_vecs.values():
        if len(vecs) < 2:
            continue
        sims = []
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                v1, v2 = vecs[i], vecs[j]
                n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
                if n1 > 0 and n2 > 0:
                    sims.append(float(v1 @ v2 / (n1 * n2)))
        if sims:
            per_model_means.append(float(np.mean(sims)))
    return float(np.mean(per_model_means)) if per_model_means else 0.0


def zscore(value: float, arr: np.ndarray) -> float:
    std = arr.std()
    return float((value - arr.mean()) / std) if std > 1e-12 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute posterior constraint index per task")
    parser.add_argument("--archive-dir", type=Path, default=Path(".clawbench/run_cache"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--tier", choices=["tier1", "tier2", "tier3", "tier4", "tier5"], default=None)
    args = parser.parse_args()

    grouped = load_task_runs_by_model(args.archive_dir, tier=args.tier)
    if not grouped:
        raise SystemExit(f"No cached runs found under {args.archive_dir}")

    per_task_texts: dict[str, list[str]] = defaultdict(list)
    per_task_model_texts: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    use_fallback_messages = False
    for model_name, task_runs in grouped.items():
        for task_id, runs in task_runs.items():
            for run in runs:
                text = _assistant_trajectory_text(run)
                if text:
                    per_task_texts[task_id].append(text)
                    per_task_model_texts[task_id][model_name].append(text)

    all_texts = [text for texts in per_task_texts.values() for text in texts]
    if not all_texts:
        use_fallback_messages = True
        for model_name, task_runs in grouped.items():
            for task_id, runs in task_runs.items():
                for run in runs:
                    text = _fallback_text_from_any_message(run)
                    if text:
                        per_task_texts[task_id].append(text)
                        per_task_model_texts[task_id][model_name].append(text)
        all_texts = [text for texts in per_task_texts.values() for text in texts]

    if not all_texts:
        raise SystemExit("No usable text found in cached transcripts.")

    vocab = build_vocab(all_texts, top_k=500)
    per_task: dict[str, dict[str, float | str]] = {}
    for task_id, texts in sorted(per_task_texts.items()):
        X = np.stack([vectorize(text, vocab) for text in texts])
        pr = participation_ratio(X)
        ent = response_entropy(X)
        model_vecs = {
            model_name: [vectorize(text, vocab) for text in model_texts]
            for model_name, model_texts in per_task_model_texts[task_id].items()
        }
        bops = bops_inter_run_predictability(model_vecs)
        per_task[task_id] = {
            "n_responses": len(texts),
            "PR": pr,
            "entropy": ent,
            "BOPS": bops,
            "data_source": "fallback_any_message" if use_fallback_messages else "assistant_final",
        }

    if not per_task:
        raise SystemExit("Not enough data to compute C(q).")

    prs = np.array([v["PR"] for v in per_task.values()])
    ents = np.array([v["entropy"] for v in per_task.values()])
    bopss = np.array([v["BOPS"] for v in per_task.values()])

    for task_id, v in per_task.items():
        z_pr = zscore(v["PR"], prs)
        z_ent = zscore(v["entropy"], ents)
        z_bops = zscore(v["BOPS"], bopss)
        v["z_PR"] = z_pr
        v["z_entropy"] = z_ent
        v["z_BOPS"] = z_bops
        v["C_q"] = -z_pr - z_ent + z_bops

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.reports_dir / "constraint_index.json"
    out_path.write_text(json.dumps(per_task, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
