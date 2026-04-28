"""Tests for clawbench.dynamics."""

from __future__ import annotations


import numpy as np
import pytest

from clawbench.dynamics import (
    Regime,
    _classify_tool,
    _cosine_dist,
    _entropy,
    _js_divergence,
    _levenshtein,
    build_strata,
    compute_dynamics,
    compute_sensitivity,
    find_event_step,
    kaplan_meier,
    stratify_by_tier,
)
from clawbench.schemas import (
    TokenUsage,
    ToolCall,
    Transcript,
    TranscriptMessage,
    TaskRunResult,
)


# ── helpers ──────────────────────────────────────────────────────────


def _msg(role, text="", family=None, success=True, error="", ts=0, tok=100):
    tcs = []
    if family:
        tcs.append(ToolCall(
            name=f"tool_{family}", family=family,
            success=success, error=error, mutating=family == "edit",
        ))
    return TranscriptMessage(
        role=role, text=text, tool_calls=tcs, timestamp_ms=ts,
        usage=TokenUsage(input_tokens=tok, output_tokens=tok // 2,
                         total_tokens=tok + tok // 2),
    )


def _simple_transcript(families, errors=None):
    if errors is None:
        errors = [False] * len(families)
    msgs = [_msg("user", "task")]
    for i, (fam, err) in enumerate(zip(families, errors)):
        msgs.append(_msg("assistant", f"step {i}", family=fam,
                         success=not err, error="err" if err else "",
                         ts=(i + 1) * 1000, tok=100 + i * 10))
    return Transcript(messages=msgs)


def _run(transcript, score=0.5, task_id="t1"):
    return TaskRunResult(
        task_id=task_id, run_index=0, transcript=transcript,
        run_score=score, duration_ms=10000,
        token_usage=transcript.total_usage,
    )


# ── _cosine_dist ─────────────────────────────────────────────────────


def test_cosine_dist_identical():
    a = np.array([1.0, 0.0, 0.5])
    assert _cosine_dist(a, a) == pytest.approx(0.0, abs=1e-9)


def test_cosine_dist_orthogonal():
    assert _cosine_dist(np.array([1, 0, 0.0]), np.array([0, 1, 0.0])) == pytest.approx(1.0)


def test_cosine_dist_zero_vector():
    assert _cosine_dist(np.zeros(3), np.array([1, 2, 3.0])) == 1.0


# ── _entropy ─────────────────────────────────────────────────────────


def test_entropy_uniform():
    assert _entropy({"a": 10, "b": 10}) == pytest.approx(1.0)


def test_entropy_single():
    assert _entropy({"a": 100}) == pytest.approx(0.0)


def test_entropy_empty():
    assert _entropy({}) == 0.0


# ── _js_divergence ───────────────────────────────────────────────────


def test_jsd_identical():
    d = {"a": 5, "b": 5}
    assert _js_divergence(d, d) == pytest.approx(0.0, abs=1e-9)


def test_jsd_disjoint():
    assert _js_divergence({"a": 10}, {"b": 10}) > 0.5


# ── _levenshtein ────────────────────────────────────────────────────


def test_levenshtein_equal():
    assert _levenshtein([1, 2, 3], [1, 2, 3]) == 0


def test_levenshtein_empty():
    assert _levenshtein([], [1, 2]) == 2


def test_levenshtein_different():
    assert _levenshtein(["a", "b"], ["c", "d"]) == 2


# ── _classify_tool ──────────────────────────────────────────────────


@pytest.mark.parametrize("name,expected", [
    ("bash_execute", "execute"),
    ("file_read", "read"),
    ("tool_edit", "edit"),
    ("web_browser", "browser"),
    ("grep_search", "search"),
    ("write_file", "edit"),
    ("run_tests", "execute"),
])
def test_classify_tool(name, expected):
    assert _classify_tool(name) == expected


# ── compute_dynamics ─────────────────────────────────────────────────


def test_dynamics_basic():
    t = _simple_transcript(["read", "edit", "execute", "read", "edit"])
    d = compute_dynamics(t)
    assert d.n_steps == 5
    assert len(d.drift) == 5
    assert len(d.step_size) == 5
    assert len(d.entropy_series) == 5
    assert len(d.tool_sequence) == 5
    assert d.tool_entropy > 0


def test_dynamics_empty():
    t = Transcript(messages=[_msg("user", "hi")])
    d = compute_dynamics(t)
    assert d.n_steps == 0
    assert d.regime == Regime.unknown


def test_dynamics_trapped():
    t = _simple_transcript(["execute"] * 15, errors=[True] * 15)
    d = compute_dynamics(t)
    assert d.regime == Regime.trapped
    assert d.error_rate > 0.5


def test_dynamics_convergent():
    cycle = ["read", "search", "edit", "read", "execute"] * 6
    t = _simple_transcript(cycle[:30])
    d = compute_dynamics(t)
    assert d.regime in (Regime.convergent, Regime.limit_cycle, Regime.diffusive, Regime.unknown)
    assert d.error_rate == 0.0


def test_dynamics_markov_keys():
    t = _simple_transcript(["read", "edit", "read"])
    d = compute_dynamics(t)
    assert "read" in d.markov
    assert "edit" in d.markov["read"]


def test_dynamics_constraint_index_range():
    t = _simple_transcript(["read", "edit", "search", "execute", "browser", "memory"] * 3)
    d = compute_dynamics(t)
    assert 0 <= d.constraint_index <= 1


def test_dynamics_memory_depth():
    t = _simple_transcript(["read", "edit", "read", "edit", "read", "edit"] * 3)
    d = compute_dynamics(t)
    assert d.memory_depth >= 0


def test_dynamics_normalizes_unknown_tool_family():
    transcript = Transcript(
        messages=[
            _msg("user", "task"),
            TranscriptMessage(
                role="assistant",
                text="searching",
                tool_calls=[
                    ToolCall(
                        name="grep_search",
                        family="unknown",
                        success=True,
                        error="",
                        mutating=False,
                    )
                ],
                timestamp_ms=1000,
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            ),
            _msg("assistant", "next", family="read", ts=2000),
            _msg("assistant", "done", family="edit", ts=3000),
        ]
    )

    dynamics = compute_dynamics(transcript)

    assert dynamics.tool_sequence[0] == "search"
    assert "search" in dynamics.markov


# ── compute_sensitivity ──────────────────────────────────────────────


def test_sensitivity_identical_runs():
    t = _simple_transcript(["read", "edit", "execute"])
    ra = _run(t, score=0.8)
    rb = _run(t, score=0.8)
    s = compute_sensitivity(ra, rb)
    assert s.score_delta == pytest.approx(0.0)
    assert s.tool_edit_distance == 0


def test_sensitivity_different_runs():
    ta = _simple_transcript(["read", "edit", "execute"])
    tb = _simple_transcript(["search", "browser", "memory"])
    ra = _run(ta, score=0.9)
    rb = _run(tb, score=0.3)
    s = compute_sensitivity(ra, rb)
    assert s.score_delta == pytest.approx(0.6)
    assert s.tool_edit_distance > 0
    assert s.family_js_divergence > 0


# ── kaplan_meier ─────────────────────────────────────────────────────


def test_km_basic():
    pts = kaplan_meier([1, 2, 3])
    assert pts[0].time == 0.0
    assert pts[0].survival == 1.0
    assert pts[-1].survival == pytest.approx(0.0)


def test_km_with_censoring():
    pts = kaplan_meier([1, 5, 3], censored=[False, True, False])
    assert len(pts) == 3
    assert pts[-1].survival > 0


def test_km_empty():
    assert kaplan_meier([]) == []


# ── find_event_step ──────────────────────────────────────────────────


def test_find_first_correct_write():
    t = _simple_transcript(["read", "search", "edit", "execute"])
    assert find_event_step(t, "first_correct_write") == 2.0


def test_find_first_error_recovery():
    t = _simple_transcript(
        ["read", "execute", "read"],
        errors=[False, True, False],
    )
    assert find_event_step(t, "first_error_recovery") == 2.0


def test_find_task_completion():
    t = _simple_transcript(["read", "edit"])
    assert find_event_step(t, "task_completion") == 1.0


def test_find_event_none():
    t = _simple_transcript(["read", "read"])
    assert find_event_step(t, "first_correct_write") is None


# ── build_strata + reweight ──────────────────────────────────────────


def test_build_strata_by_tier():
    runs, dyns, scores = [], [], []
    for tid, sc in [("t1-a", 0.8), ("t1-b", 0.6), ("t2-a", 0.4), ("t2-b", 0.3)]:
        t = _simple_transcript(["read", "edit", "execute"])
        r = _run(t, score=sc, task_id=tid)
        runs.append(r)
        dyns.append(compute_dynamics(t))
        scores.append(sc)

    sa = build_strata(runs, dyns, scores, stratify_by_tier, "tier")
    assert sa.total_runs == 4
    names = sa.stratum_names()
    assert "tier1" in names
    assert "tier2" in names
    for s in sa.strata:
        assert s.n_runs == 2
        assert s.weight == pytest.approx(0.5)


def test_reweight_shifts_mean():
    runs, dyns, scores = [], [], []
    for tid, sc in [("t1-a", 0.9), ("t1-b", 0.8), ("t2-a", 0.2), ("t2-b", 0.1)]:
        t = _simple_transcript(["read", "edit", "execute"])
        r = _run(t, score=sc, task_id=tid)
        runs.append(r)
        dyns.append(compute_dynamics(t))
        scores.append(sc)

    sa = build_strata(runs, dyns, scores, stratify_by_tier, "tier")

    # Reweight towards tier1 (high scores)
    high = sa.reweight({"tier1": 0.9, "tier2": 0.1})
    # Reweight towards tier2 (low scores)
    low = sa.reweight({"tier1": 0.1, "tier2": 0.9})

    assert high["score_mean"] > low["score_mean"]


def test_reweight_unknown_stratum():
    runs, dyns, scores = [], [], []
    t = _simple_transcript(["read", "edit"])
    r = _run(t, score=0.5, task_id="t1-x")
    runs.append(r)
    dyns.append(compute_dynamics(t))
    scores.append(0.5)

    sa = build_strata(runs, dyns, scores, stratify_by_tier, "tier")
    # Reweight with a stratum that doesn't exist — should fall back
    result = sa.reweight({"nonexistent": 1.0})
    assert "score_mean" in result
