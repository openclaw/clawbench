"""Dynamics analysis for ClawBench agent trajectories.

Treats each agent run as a discrete dynamical system and computes step
embeddings, trajectory metrics, sensitivity analysis, regime classification,
Kaplan-Meier survival, non-Markov memory, and stratified assessment with
Bayesian importance-weight correction for distribution shift.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

import numpy as np

if TYPE_CHECKING:
    from clawbench.schemas import TaskRunResult, Transcript

# ── Constants ──────────────────────────────────────────────────────────

TOOL_FAMILIES = ("browser", "edit", "execute", "memory", "read", "search")
_N_FAM = len(TOOL_FAMILIES)

# ── Types ──────────────────────────────────────────────────────────────


class Regime(str, Enum):
    convergent = "convergent"
    chaotic = "chaotic"
    trapped = "trapped"
    diffusive = "diffusive"
    limit_cycle = "limit_cycle"
    unknown = "unknown"


@dataclass
class Dynamics:
    """Computed dynamics for a single trajectory."""

    n_steps: int
    embeddings: np.ndarray          # (n_steps, 10)
    drift: np.ndarray               # cosine distance from step 0
    step_size: np.ndarray           # cosine distance from step t-1
    entropy_series: list[float]     # running tool-family entropy
    error_rate_series: list[float]  # running error fraction
    tokens_series: list[int]
    latency_series: list[float]
    tool_sequence: list[str]        # primary family per step
    markov: dict[str, dict[str, float]]
    family_dist: dict[str, float]
    regime: Regime
    mean_drift: float
    mean_step_size: float
    tool_entropy: float
    error_rate: float
    constraint_index: float
    pca_trajectory: np.ndarray | None = None  # (n_steps, 2)
    bigram_transitions: dict[str, dict[str, float]] = field(default_factory=dict)
    memory_depth: float = 0.0       # I(X_t; X_{t-2} | X_{t-1})


@dataclass
class Sensitivity:
    """Pairwise comparison between two runs of the same task."""

    task_id: str
    score_delta: float
    tool_edit_distance: int
    family_js_divergence: float
    embedding_divergence: np.ndarray  # (min_steps,)
    lyapunov_proxy: float


@dataclass
class SurvivalPoint:
    time: float
    survival: float


# ── Helpers ────────────────────────────────────────────────────────────


def _cosine_dist(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 1.0
    return float(1.0 - np.dot(a, b) / (na * nb))


def _entropy(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -sum(
        (c / total) * math.log2(c / total) for c in counts.values() if c > 0
    )


def _js_divergence(p: dict[str, int], q: dict[str, int]) -> float:
    keys = set(p) | set(q)
    if not keys:
        return 0.0
    tp, tq = sum(p.values()) or 1, sum(q.values()) or 1
    jsd = 0.0
    for k in keys:
        pk, qk = p.get(k, 0) / tp, q.get(k, 0) / tq
        mk = (pk + qk) / 2
        if pk > 0 and mk > 0:
            jsd += 0.5 * pk * math.log2(pk / mk)
        if qk > 0 and mk > 0:
            jsd += 0.5 * qk * math.log2(qk / mk)
    return jsd


def _levenshtein(a: list, b: list) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1] + [0] * len(b)
        for j, cb in enumerate(b):
            curr[j + 1] = min(
                prev[j] + (0 if ca == cb else 1),
                prev[j + 1] + 1,
                curr[j] + 1,
            )
        prev = curr
    return prev[-1]


def _classify_tool(name: str) -> str:
    lo = name.lower()
    for fam in TOOL_FAMILIES:
        if fam in lo:
            return fam
    _ALIASES = {
        "edit": ("write_file", "create_file", "str_replace", "patch"),
        "execute": ("bash", "terminal", "shell", "run", "exec"),
        "browser": ("browse", "click", "navigate", "screenshot"),
        "search": ("grep", "find", "glob", "semantic"),
        "read": ("cat", "head", "tail", "view", "list_dir"),
    }
    for fam, keywords in _ALIASES.items():
        if any(k in lo for k in keywords):
            return fam
    return "execute"


def _normalize_tool_family(name: str, family: str | None) -> str:
    if family in TOOL_FAMILIES:
        return family
    return _classify_tool(name)


# ── Feature embedding ──────────────────────────────────────────────────


def _embed_transcript(
    transcript: Transcript,
) -> tuple[np.ndarray, list[str], list[int], list[float], list[bool]]:
    """Build (n_steps, 10) feature matrix from assistant turns.

    Features: [0:6] tool-family proportions, [6] error flag,
    [7] normalised tokens, [8] normalised text length, [9] progress.
    """
    msgs = transcript.assistant_messages
    n = len(msgs)
    if n == 0:
        return np.empty((0, _N_FAM + 4)), [], [], [], []

    X = np.zeros((n, _N_FAM + 4))
    families: list[str] = []
    tokens: list[int] = []
    latencies: list[float] = []
    errors: list[bool] = []
    raw_tokens = np.zeros(n)
    raw_text = np.zeros(n)

    for i, msg in enumerate(msgs):
        fam_counts: Counter = Counter()
        has_err = False
        for tc in msg.tool_calls:
            fam = _normalize_tool_family(tc.name, tc.family)
            fam_counts[fam] += 1
            if tc.success is False or tc.error:
                has_err = True
        n_tc = sum(fam_counts.values()) or 1
        for j, fam in enumerate(TOOL_FAMILIES):
            X[i, j] = fam_counts.get(fam, 0) / n_tc
        X[i, _N_FAM] = 1.0 if has_err else 0.0
        X[i, _N_FAM + 3] = i / max(n - 1, 1)

        families.append(
            max(fam_counts, key=fam_counts.get) if fam_counts else "execute"
        )
        errors.append(has_err)
        tokens.append(msg.usage.total_tokens)
        raw_tokens[i] = float(msg.usage.total_tokens)
        raw_text[i] = float(len(msg.text))
        dt = msg.timestamp_ms - msgs[i - 1].timestamp_ms if i > 0 else 0
        latencies.append(max(float(dt), 0.0))

    mx_tok = raw_tokens.max() or 1
    mx_txt = raw_text.max() or 1
    X[:, _N_FAM + 1] = raw_tokens / mx_tok
    X[:, _N_FAM + 2] = raw_text / mx_txt

    return X, families, tokens, latencies, errors


# ── Non-Markov memory ────────────────────────────────────────────────


def _compute_bigram_transitions(seq: list[str]) -> dict[str, dict[str, float]]:
    """P(family_t | family_{t-1}, family_{t-2}) grouped by bigram context."""
    if len(seq) < 3:
        return {}
    bigrams: dict[str, Counter] = {}
    for a, b, c in zip(seq[:-2], seq[1:-1], seq[2:]):
        ctx = f"{a}->{b}"
        bigrams.setdefault(ctx, Counter())[c] += 1
    return {
        ctx: {k: v / sum(cnts.values()) for k, v in cnts.items()}
        for ctx, cnts in bigrams.items()
    }


def _conditional_mi(seq: list[str]) -> float:
    """I(X_t ; X_{t-2} | X_{t-1}) — non-Markov msemory indicator."""
    if len(seq) < 3:
        return 0.0
    n = len(seq) - 2
    triple = Counter(zip(seq[:-2], seq[1:-1], seq[2:]))
    pair_01 = Counter(zip(seq[:-2], seq[1:-1]))
    pair_12 = Counter(zip(seq[1:-1], seq[2:]))
    single = Counter(seq[1:-1])

    mi = 0.0
    for (a, b, c), count in triple.items():
        p_abc = count / n
        p_ab, p_bc, p_b = pair_01[(a, b)] / n, pair_12[(b, c)] / n, single[b] / n
        if p_ab > 0 and p_bc > 0 and p_b > 0:
            mi += p_abc * math.log2((p_abc * p_b) / (p_ab * p_bc))
    return max(mi, 0.0)


# ── Core analysis ──────────────────────────────────────────────────────


def compute_dynamics(transcript: Transcript) -> Dynamics:
    """Compute trajectory dynamics from a single run transcript."""
    X, families, tokens, latencies, errors = _embed_transcript(transcript)
    n = len(families)

    drift = (
        np.array([_cosine_dist(X[0], X[i]) for i in range(n)])
        if n else np.array([])
    )
    step_sz = np.zeros(n)
    for i in range(1, n):
        step_sz[i] = _cosine_dist(X[i - 1], X[i])

    fam_acc: Counter = Counter()
    err_count = 0
    entropy_s: list[float] = []
    error_s: list[float] = []
    for i, (fam, err) in enumerate(zip(families, errors)):
        fam_acc[fam] += 1
        err_count += int(err)
        entropy_s.append(_entropy(dict(fam_acc)))
        error_s.append(err_count / (i + 1))

    total = sum(fam_acc.values()) or 1
    fam_dist = {k: v / total for k, v in fam_acc.items()}

    mc: dict[str, Counter] = {f: Counter() for f in TOOL_FAMILIES}
    for a, b in zip(families[:-1], families[1:]):
        mc[a][b] += 1
    markov = {
        src: ({dst: c / t for dst, c in cnts.items()} if (t := sum(cnts.values())) else {})
        for src, cnts in mc.items()
    }

    ci = 0.5
    if n > 2:
        cov = np.cov(X.T)
        eigvals = np.maximum(np.linalg.eigvalsh(cov), 0)
        tv = eigvals.sum()
        if tv > 1e-10:
            p = eigvals / tv
            pr = 1.0 / np.sum(p**2)
            ci = 1.0 - (pr - 1) / (X.shape[1] - 1)

    h = _entropy(dict(fam_acc))
    er = err_count / n if n else 0
    regime = _classify_regime(drift, step_sz, h, er, ci, n)

    return Dynamics(
        n_steps=n,
        embeddings=X,
        drift=drift,
        step_size=step_sz,
        entropy_series=entropy_s,
        error_rate_series=error_s,
        tokens_series=tokens,
        latency_series=latencies,
        tool_sequence=families,
        markov=markov,
        family_dist=fam_dist,
        regime=regime,
        mean_drift=float(np.mean(drift)) if n else 0,
        mean_step_size=float(np.mean(step_sz)) if n else 0,
        tool_entropy=h,
        error_rate=er,
        constraint_index=ci,
        bigram_transitions=_compute_bigram_transitions(families),
        memory_depth=_conditional_mi(families),
    )


def _classify_regime(drift, step_sz, entropy, error_rate, ci, n) -> Regime:
    if n < 3:
        return Regime.unknown
    if entropy < 0.5 or (error_rate > 0.6 and float(np.std(drift)) < 0.05):
        return Regime.trapped
    q = max(1, n // 4)
    late_drift_std = float(np.std(drift[-q:]))
    late_step_mean = float(np.mean(step_sz[-q:]))
    if late_drift_std < 0.1 and late_step_mean < 0.15 and error_rate < 0.2:
        return Regime.convergent
    if entropy > 1.5 and error_rate < 0.15 and ci < 0.8:
        return Regime.diffusive
    step_var = float(np.var(step_sz[1:])) if n > 1 else 0
    if entropy > 2.0 and step_var > 0.02:
        return Regime.chaotic
    if n > 6:
        ss = step_sz[1:]
        ss_c = ss - ss.mean()
        norm = np.dot(ss_c, ss_c)
        if norm > 1e-10:
            ac = np.correlate(ss_c, ss_c, mode="full")
            ac = ac[len(ac) // 2:] / norm
            if len(ac) > 5 and max(ac[2:6]) > 0.3:
                return Regime.limit_cycle
    return Regime.unknown


# ── Sensitivity ────────────────────────────────────────────────────────


def compute_sensitivity(
    run_a: TaskRunResult,
    run_b: TaskRunResult,
    task_id: str = "",
) -> Sensitivity:
    """Compare two runs of the same task for prompt sensitivity."""
    Xa, fam_a, *_ = _embed_transcript(run_a.transcript)
    Xb, fam_b, *_ = _embed_transcript(run_b.transcript)

    min_n = min(len(Xa), len(Xb))
    emb_div = (
        np.array([_cosine_dist(Xa[i], Xb[i]) for i in range(min_n)])
        if min_n else np.array([])
    )

    lyap = 0.0
    if min_n > 1:
        d0 = max(_cosine_dist(Xa[0], Xb[0]), 1e-6)
        lyap = sum(
            math.log(max(emb_div[t], 1e-6) / d0) / t for t in range(1, min_n)
        ) / (min_n - 1)

    return Sensitivity(
        task_id=task_id or run_a.task_id,
        score_delta=abs(run_a.run_score - run_b.run_score),
        tool_edit_distance=_levenshtein(fam_a, fam_b),
        family_js_divergence=_js_divergence(dict(Counter(fam_a)), dict(Counter(fam_b))),
        embedding_divergence=emb_div,
        lyapunov_proxy=lyap,
    )


# ── Survival analysis ─────────────────────────────────────────────────


def kaplan_meier(
    event_times: list[float],
    censored: list[bool] | None = None,
) -> list[SurvivalPoint]:
    """Kaplan-Meier survival estimator."""
    n = len(event_times)
    if n == 0:
        return []
    if censored is None:
        censored = [False] * n
    pairs = sorted(zip(event_times, censored))
    pts = [SurvivalPoint(0.0, 1.0)]
    at_risk = n
    surv = 1.0
    for t, cens in pairs:
        if cens:
            at_risk -= 1
            continue
        if at_risk > 0:
            surv *= (at_risk - 1) / at_risk
        at_risk -= 1
        pts.append(SurvivalPoint(t, surv))
    return pts


def find_event_step(transcript: Transcript, event: str) -> float | None:
    """Return step index of the first occurrence of *event*, or None."""
    msgs = transcript.assistant_messages
    if event == "first_error_recovery":
        in_err = False
        for i, m in enumerate(msgs):
            any_err = any(tc.success is False or tc.error for tc in m.tool_calls)
            if any_err:
                in_err = True
            elif in_err:
                return float(i)
    elif event == "first_correct_write":
        for i, m in enumerate(msgs):
            for tc in m.tool_calls:
                fam = tc.family or _classify_tool(tc.name)
                if fam == "edit" and tc.success is not False and not tc.error:
                    return float(i)
    elif event == "task_completion":
        if msgs:
            last = msgs[-1]
            if not any(tc.success is False or tc.error for tc in last.tool_calls):
                return float(len(msgs) - 1)
    elif event == "failure_absorption":
        err_seen = False
        for i, m in enumerate(msgs):
            any_err = any(tc.success is False or tc.error for tc in m.tool_calls)
            if any_err:
                err_seen = True
            elif err_seen and m.tool_calls:
                return float(i)
    return None


# ── PCA trajectory bundles ─────────────────────────────────────────────


def compute_pca_bundle(
    dynamics_list: list[Dynamics],
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Fit PCA on pooled embeddings, project each trajectory into PC1-PC2."""
    non_empty = [d.embeddings for d in dynamics_list if d.n_steps > 0]
    if not non_empty:
        for d in dynamics_list:
            d.pca_trajectory = np.empty((0, 2))
        return np.zeros((2, _N_FAM + 4)), []
    all_emb = np.vstack(non_empty)
    mean = all_emb.mean(axis=0)
    centred = all_emb - mean
    _, _, Vt = np.linalg.svd(centred, full_matrices=False)
    components = Vt[:2]

    projections: list[np.ndarray] = []
    for d in dynamics_list:
        proj = (d.embeddings - mean) @ components.T if d.n_steps else np.empty((0, 2))
        d.pca_trajectory = proj
        projections.append(proj)
    return components, projections


# ── Stratified assessment with Bayesian reweighting ───────────────────


@dataclass
class StratumStats:
    """Distributional statistics for one stratum of runs."""

    name: str
    n_runs: int
    weight: float

    # Score distribution
    scores: np.ndarray
    score_mean: float
    score_std: float
    score_quantiles: dict[str, float]  # q10, q25, q50, q75, q90

    # Dynamics distributions
    entropy_dist: np.ndarray
    error_rate_dist: np.ndarray
    constraint_dist: np.ndarray
    memory_depth_dist: np.ndarray
    mean_drift_dist: np.ndarray
    mean_step_size_dist: np.ndarray

    # Time-series curves (aligned by step index)
    drift_curve_mean: np.ndarray
    drift_curve_std: np.ndarray
    step_curve_mean: np.ndarray
    step_curve_std: np.ndarray

    regime_counts: dict[str, int]
    sensitivity_deltas: np.ndarray


# Scalar fields on StratumStats that reweight() aggregates.
_REWEIGHT_FIELDS = [
    ("entropy", "entropy_dist"),
    ("error_rate", "error_rate_dist"),
    ("constraint", "constraint_dist"),
    ("memory_depth", "memory_depth_dist"),
    ("mean_drift", "mean_drift_dist"),
    ("mean_step_size", "mean_step_size_dist"),
]


@dataclass
class StratifiedAssessment:
    """Full stratified assessment with Bayesian reweighting.

    Call ``reweight(target_weights)`` with a different task distribution
    to obtain importance-weighted aggregate estimates.
    """

    strata: list[StratumStats]
    stratifier_name: str
    total_runs: int
    observed_mean_score: float
    observed_std_score: float

    def stratum_names(self) -> list[str]:
        return [s.name for s in self.strata]

    def reweight(self, target_weights: dict[str, float]) -> dict[str, float]:
        """Bayesian importance-weight correction.

        w_k = p_target(k) / p_observed(k), then normalised.
        """
        t_total = sum(target_weights.values()) or 1.0
        p_target = {k: v / t_total for k, v in target_weights.items()}
        by_name = {s.name: s for s in self.strata}

        weights = {
            name: pt / by_name[name].weight
            for name, pt in p_target.items()
            if name in by_name and by_name[name].weight > 1e-12
        }
        if not weights:
            return {"score_mean": self.observed_mean_score,
                    "score_std": self.observed_std_score}

        w_total = sum(weights.values())
        w = {k: v / w_total for k, v in weights.items()}

        # Reweight score (mean + law-of-total-variance)
        score_mu = sum(w[k] * by_name[k].score_mean for k in w)
        score_var = sum(
            w[k] * (by_name[k].score_std ** 2 + (by_name[k].score_mean - score_mu) ** 2)
            for k in w
        )
        result = {"score_mean": score_mu, "score_std": math.sqrt(max(score_var, 0.0))}

        def _safe_mean(arr: np.ndarray) -> float:
            return float(np.mean(arr)) if len(arr) > 0 else 0.0

        for label, dist_attr in _REWEIGHT_FIELDS:
            result[f"{label}_mean"] = sum(
                w[k] * _safe_mean(getattr(by_name[k], dist_attr)) for k in w
            )
        return result


def _aligned_mean_std(arrays: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Mean and std of variable-length arrays aligned at step 0."""
    if not arrays:
        return np.array([]), np.array([])
    max_len = max(len(a) for a in arrays)
    mat = np.full((len(arrays), max_len), np.nan)
    for i, a in enumerate(arrays):
        mat[i, :len(a)] = a
    return np.nanmean(mat, axis=0), np.nanstd(mat, axis=0)


def build_strata(
    runs: list[TaskRunResult],
    dynamics_list: list[Dynamics],
    scores: list[float],
    stratifier: Callable[[TaskRunResult, Dynamics], str],
    stratifier_name: str = "custom",
    sensitivities: list[Sensitivity] | None = None,
) -> StratifiedAssessment:
    """Group runs into strata and compute per-stratum distributions."""
    assert len(runs) == len(dynamics_list) == len(scores)

    groups: dict[str, list[int]] = {}
    for idx, (r, d) in enumerate(zip(runs, dynamics_list)):
        groups.setdefault(stratifier(r, d), []).append(idx)

    total = len(runs)
    all_scores = np.array(scores)

    sens_by_task: dict[str, list[Sensitivity]] = {}
    if sensitivities:
        for s in sensitivities:
            sens_by_task.setdefault(s.task_id, []).append(s)

    strata: list[StratumStats] = []
    for name, idxs in sorted(groups.items()):
        n = len(idxs)
        sc = np.array([scores[i] for i in idxs])
        dyns = [dynamics_list[i] for i in idxs]

        qs = {f"q{q}": float(np.percentile(sc, q)) if n else 0.0
              for q in (10, 25, 50, 75, 90)}

        drift_m, drift_s = _aligned_mean_std([d.drift for d in dyns])
        step_m, step_s = _aligned_mean_std([d.step_size for d in dyns])

        stratum_tasks = {runs[i].task_id for i in idxs}
        sens_deltas = [
            s.score_delta
            for tid in stratum_tasks
            for s in sens_by_task.get(tid, [])
        ]

        strata.append(StratumStats(
            name=name, n_runs=n, weight=n / total if total else 0.0,
            scores=sc,
            score_mean=float(np.mean(sc)) if n else 0.0,
            score_std=float(np.std(sc)) if n else 0.0,
            score_quantiles=qs,
            entropy_dist=np.array([d.tool_entropy for d in dyns]),
            error_rate_dist=np.array([d.error_rate for d in dyns]),
            constraint_dist=np.array([d.constraint_index for d in dyns]),
            memory_depth_dist=np.array([d.memory_depth for d in dyns]),
            mean_drift_dist=np.array([d.mean_drift for d in dyns]),
            mean_step_size_dist=np.array([d.mean_step_size for d in dyns]),
            drift_curve_mean=drift_m, drift_curve_std=drift_s,
            step_curve_mean=step_m, step_curve_std=step_s,
            regime_counts=dict(Counter(d.regime.value for d in dyns)),
            sensitivity_deltas=np.array(sens_deltas) if sens_deltas else np.array([]),
        ))

    return StratifiedAssessment(
        strata=strata,
        stratifier_name=stratifier_name,
        total_runs=total,
        observed_mean_score=float(np.mean(all_scores)) if total else 0.0,
        observed_std_score=float(np.std(all_scores)) if total else 0.0,
    )


# ── Built-in stratifiers ──────────────────────────────────────────────


def stratify_by_regime(run: TaskRunResult, dyn: Dynamics) -> str:
    return dyn.regime.value


def stratify_by_task(run: TaskRunResult, dyn: Dynamics) -> str:
    return run.task_id


def stratify_by_tier(run: TaskRunResult, dyn: Dynamics) -> str:
    tid = run.task_id.lower()
    for i in range(1, 6):
        if tid.startswith(f"t{i}_") or tid.startswith(f"t{i}-"):
            return f"tier{i}"
    return "unknown"


def stratify_by_tool_mix(run: TaskRunResult, dyn: Dynamics) -> str:
    if not dyn.family_dist:
        return "unknown"
    return max(dyn.family_dist, key=dyn.family_dist.get)


def stratify_by_prompt_style(run: TaskRunResult, dyn: Dynamics) -> str:
    user_msgs = [m for m in run.transcript.messages if m.role == "user"]
    if not user_msgs:
        return "unknown"
    wc = len(user_msgs[0].text.split())
    return "terse" if wc <= 6 else ("medium" if wc <= 15 else "verbose")


def stratify_by_scenario(run: TaskRunResult, dyn: Dynamics) -> str:
    return run.scenario or "unknown"


def stratify_by_family(run: TaskRunResult, dyn: Dynamics) -> str:
    return run.family or "unknown"
