from clawbench.ablation import (
    common_compatible_task_set,
    compare_results,
    default_tool_profile,
)
from clawbench.adapters.hermes import HermesAdapterConfig
from clawbench.schemas import (
    BenchmarkResult,
    CompletionSpec,
    FileState,
    SimulatedUser,
    TaskDefinition,
    TaskFamily,
    TaskStats,
    Tier,
    UserTurn,
)


def _task(task_id: str) -> TaskDefinition:
    return TaskDefinition(
        id=task_id,
        name=task_id,
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        user=SimulatedUser(turns=[UserTurn(message="write out.txt")]),
        completion=CompletionSpec(files=[FileState(path="out.txt")]),
    )


def test_tool_profile_fingerprint_is_stable() -> None:
    config = HermesAdapterConfig(driver_mode="ai_agent", enabled_toolsets=["hermes-api-server"])
    a = default_tool_profile(adapter="hermes", config=config, enabled_toolsets=["hermes-api-server"])
    b = default_tool_profile(adapter="hermes", config=config, enabled_toolsets=["hermes-api-server"])

    assert a.fingerprint == b.fingerprint
    assert "browser" in a.interfaces
    assert "multi_turn" in a.interfaces


def test_common_compatible_task_set_uses_effective_adapter_config() -> None:
    tasks = [_task("a"), _task("b")]
    plan = common_compatible_task_set(
        tasks,
        {
            "openclaw": ("openclaw", None),
            "hermes": ("hermes", HermesAdapterConfig(driver_mode="ai_agent")),
        },
    )

    assert plan.task_ids == ["a", "b"]
    assert plan.skipped == {}


def _result(label: str, model: str, task_ids: list[str], score: float) -> BenchmarkResult:
    task_results = [
        TaskStats(
            task_id=task_id,
            tier="tier1",
            family="coding",
            runs=1,
            mean_completion_score=1.0,
            mean_trajectory_score=1.0,
            mean_behavior_score=1.0,
            mean_run_score=score,
            reliability_score=1.0,
            variance_score=1.0,
            mean_task_score=score,
            stddev=0.0,
            min_score=score,
            max_score=score,
            pass_at_1=True,
            pass_rate=1.0,
            pass_hat_k=True,
        )
        for task_id in task_ids
    ]
    return BenchmarkResult(
        submission_id=label,
        model=model,
        provider="test",
        timestamp="2026-04-25T00:00:00Z",
        overall_score=score,
        overall_completion=1.0,
        overall_trajectory=1.0,
        overall_behavior=1.0,
        overall_reliability=1.0,
        overall_ci_lower=score,
        overall_ci_upper=score,
        overall_pass_hat_k=1.0,
        task_results=task_results,
    )


def test_compare_results_rejects_different_task_sets() -> None:
    comparison = compare_results(
        {
            "a": _result("a", "m", ["t1", "t2"], 0.8),
            "b": _result("b", "m", ["t1"], 0.9),
        }
    )

    assert comparison["fair"] is False
    assert comparison["task_verifier_fair"] is False
    assert comparison["controlled_ablation"] is False
    assert comparison["same_model"] is True
    assert comparison["same_task_set"] is False


def test_compare_results_allows_cross_model_same_task_leaderboard() -> None:
    a = _result("a", "model-a", ["t1", "t2"], 0.8)
    b = _result("b", "model-b", ["t1", "t2"], 0.9)
    a.task_snapshot_fingerprint = "snapshot-1"
    b.task_snapshot_fingerprint = "snapshot-1"

    comparison = compare_results({"a": a, "b": b})

    assert comparison["fair"] is True
    assert comparison["task_verifier_fair"] is True
    assert comparison["controlled_ablation"] is False
    assert comparison["same_model"] is False
