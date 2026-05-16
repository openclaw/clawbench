from clawbench.scorer import (
    classify_delivery_outcome,
    classify_error_failure_mode,
    classify_failure_mode,
    combine_run_score,
    evaluate_behavior,
)
from clawbench.schemas import (
    BehaviorExpectations,
    BehaviorResult,
    CompletionResult,
    DeliveryOutcome,
    FailureMode,
    SimulatedUser,
    TaskDefinition,
    TaskFamily,
    Tier,
    ToolCall,
    Transcript,
    TranscriptMessage,
    TrajectoryResult,
    UserTurn,
)


def test_combine_run_score_uses_normalized_weighted_average():
    assert combine_run_score(completion=1.0, trajectory=1.0, behavior=1.0) == 1.0
    assert combine_run_score(completion=0.0, trajectory=0.0, behavior=0.0) == 0.0
    assert combine_run_score(completion=1.0, trajectory=0.0, behavior=0.0) == 0.4444
    assert combine_run_score(completion=0.5, trajectory=1.0, behavior=1.0) == 0.7778


def test_combine_run_score_caps_judge_when_deterministic_verifier_present():
    """Per v0.4 spec: semantic quality never rescues failed completion.

    When a task has deterministic completion checks and the deterministic
    floor is not met, the judge contribution must be zeroed, so a broken
    solution with a green judge cannot inflate the score above what the
    deterministic axes allow.
    """
    # Deterministic floor NOT met, judge reports a perfect 1.0 — judge
    # must not rescue the score. The run score should equal the gated
    # deterministic path for completion=0.5.
    with_broken_det = combine_run_score(
        completion=0.5,
        trajectory=1.0,
        behavior=1.0,
        judge=1.0,
        has_deterministic_verifier=True,
    )
    without_judge = combine_run_score(
        completion=0.5,
        trajectory=1.0,
        behavior=1.0,
        has_deterministic_verifier=True,
    )
    assert with_broken_det == without_judge


def test_combine_run_score_gates_failed_deterministic_completion():
    ungated = combine_run_score(completion=0.0, trajectory=1.0, behavior=1.0)
    gated = combine_run_score(
        completion=0.0,
        trajectory=1.0,
        behavior=1.0,
        has_deterministic_verifier=True,
    )

    assert ungated == 0.5556
    assert gated == 0.1944


def test_combine_run_score_judge_lifts_at_most_10pct_when_deterministic_passes():
    """Judge may contribute at most 10% when a deterministic verifier exists."""
    # Deterministic floor IS met (1.0), and judge is 1.0 — the judge row
    # weight is 0.10 out of total 1.0 so score should still be 1.0.
    full = combine_run_score(
        completion=1.0,
        trajectory=1.0,
        behavior=1.0,
        judge=1.0,
        has_deterministic_verifier=True,
    )
    assert full == 1.0

    # Deterministic floor met, judge = 0 — run score should only lose
    # the 10% judge contribution.
    lost_judge = combine_run_score(
        completion=1.0,
        trajectory=1.0,
        behavior=1.0,
        judge=0.0,
        has_deterministic_verifier=True,
    )
    assert abs(lost_judge - 0.9) < 1e-4


def test_combine_run_score_semantic_only_task_lets_judge_dominate():
    """When no deterministic verifier exists, the judge is allowed to drive."""
    semantic = combine_run_score(
        completion=0.0,
        trajectory=0.0,
        behavior=0.0,
        judge=1.0,
        has_deterministic_verifier=False,
    )
    # Judge weight 0.50 out of total 1.0
    assert abs(semantic - 0.5) < 1e-4


def test_evaluate_behavior_counts_later_tool_work_as_progress():
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", text="First I'll inspect the project."),
            TranscriptMessage(
                role="assistant",
                tool_calls=[ToolCall(name="exec_command", input={"cmd": "pytest -q"})],
            ),
        ]
    )

    result = evaluate_behavior(
        BehaviorExpectations(require_plan=True, require_progress_updates=True),
        transcript,
    )

    assert result.score == 1.0
    assert result.failed_expectations == []


def test_classify_failure_mode_flags_hallucinated_completion():
    task = TaskDefinition(
        id="test-task",
        name="Test Task",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        user=SimulatedUser(turns=[UserTurn(message="Fix it")]),
    )
    transcript = Transcript(messages=[TranscriptMessage(role="assistant", text="All done. Tests pass now.")])
    failure_mode = classify_failure_mode(
        task=task,
        transcript=transcript,
        completion_result=CompletionResult(total_assertions=1, passed_assertions=0, failed_assertions=["EXEC pytest: failed"]),
        trajectory_result=TrajectoryResult(self_verified=True),
        behavior_result=BehaviorResult(score=1.0),
        error=None,
    )

    assert failure_mode == FailureMode.HALLUCINATED_COMPLETION


def test_classify_failure_mode_prefers_unsafe_mutation():
    task = TaskDefinition(
        id="test-task",
        name="Test Task",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        user=SimulatedUser(turns=[UserTurn(message="Fix it")]),
    )
    failure_mode = classify_failure_mode(
        task=task,
        transcript=Transcript(),
        completion_result=CompletionResult(total_assertions=1, passed_assertions=0, failed_assertions=["EXEC pytest: failed"]),
        trajectory_result=TrajectoryResult(forbidden_violations=["Dangerous shell command: rm -rf ."]),
        behavior_result=BehaviorResult(score=0.0),
        error=None,
    )

    assert failure_mode == FailureMode.UNSAFE_MUTATION


def test_classify_failure_mode_empty_agent_output_is_infra():
    task = TaskDefinition(
        id="test-task",
        name="Test Task",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        user=SimulatedUser(turns=[UserTurn(message="Fix it")]),
    )

    failure_mode = classify_failure_mode(
        task=task,
        transcript=Transcript(),
        completion_result=CompletionResult(
            total_assertions=1,
            passed_assertions=0,
            failed_assertions=["EXEC pytest: failed"],
        ),
        trajectory_result=TrajectoryResult(),
        behavior_result=BehaviorResult(score=0.0),
        error=None,
    )

    assert failure_mode == FailureMode.ENVIRONMENT_UNAVAILABLE


def test_classify_failure_mode_aborted_only_agent_output_is_infra():
    task = TaskDefinition(
        id="test-task",
        name="Test Task",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        user=SimulatedUser(turns=[UserTurn(message="Fix it")]),
    )

    failure_mode = classify_failure_mode(
        task=task,
        transcript=Transcript(messages=[TranscriptMessage(role="assistant", text="This operation was aborted")]),
        completion_result=CompletionResult(
            total_assertions=1,
            passed_assertions=0,
            failed_assertions=["EXEC pytest: failed"],
        ),
        trajectory_result=TrajectoryResult(),
        behavior_result=BehaviorResult(score=0.0),
        error=None,
    )

    assert failure_mode == FailureMode.ENVIRONMENT_UNAVAILABLE


def test_classify_failure_mode_provider_auth_failure_is_infra():
    task = TaskDefinition(
        id="test-task",
        name="Test Task",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        user=SimulatedUser(turns=[UserTurn(message="Fix it")]),
    )

    failure_mode = classify_failure_mode(
        task=task,
        transcript=Transcript(
            messages=[
                TranscriptMessage(
                    role="assistant",
                    text='⚠️ Agent failed before reply: No API key found for provider "anthropic".',
                )
            ]
        ),
        completion_result=CompletionResult(
            total_assertions=1,
            passed_assertions=0,
            failed_assertions=["EXEC pytest: failed"],
        ),
        trajectory_result=TrajectoryResult(),
        behavior_result=BehaviorResult(score=0.0),
        error=None,
    )

    assert failure_mode == FailureMode.ENVIRONMENT_UNAVAILABLE


def test_classify_failure_mode_browser_tool_misuse_is_not_infra():
    task = TaskDefinition(
        id="browser-task",
        name="Browser Task",
        tier=Tier.TIER3,
        family=TaskFamily.BROWSER,
        surface="browser",
        user=SimulatedUser(turns=[UserTurn(message="Fix the page")]),
    )
    transcript = Transcript(
        messages=[
            TranscriptMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        name="browser",
                        family="browser",
                        success=False,
                        output="Error: page.evaluate: Invalid evaluate function",
                    )
                ],
            )
        ]
    )

    failure_mode = classify_failure_mode(
        task=task,
        transcript=transcript,
        completion_result=CompletionResult(
            total_assertions=1,
            passed_assertions=0,
            failed_assertions=["EXEC verifier: failed"],
        ),
        trajectory_result=TrajectoryResult(required_families_missing=["execute"]),
        behavior_result=BehaviorResult(score=1.0),
        error=None,
    )

    assert failure_mode == FailureMode.VERIFICATION_SKIPPED


def test_classify_failure_mode_browser_service_failure_stays_infra():
    task = TaskDefinition(
        id="browser-task",
        name="Browser Task",
        tier=Tier.TIER3,
        family=TaskFamily.BROWSER,
        surface="browser",
        user=SimulatedUser(turns=[UserTurn(message="Fix the page")]),
    )
    transcript = Transcript(
        messages=[
            TranscriptMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        name="browser",
                        family="browser",
                        success=False,
                        output="Error: could not connect to CDP: connection refused",
                    )
                ],
            )
        ]
    )

    failure_mode = classify_failure_mode(
        task=task,
        transcript=transcript,
        completion_result=CompletionResult(
            total_assertions=1,
            passed_assertions=0,
            failed_assertions=["EXEC verifier: failed"],
        ),
        trajectory_result=TrajectoryResult(required_families_missing=["execute"]),
        behavior_result=BehaviorResult(score=1.0),
        error=None,
    )

    assert failure_mode == FailureMode.BROWSER_NAVIGATION_FAILURE


def test_classify_error_failure_mode_browser_model_error_is_state_regression():
    task = TaskDefinition(
        id="browser-task",
        name="Browser Task",
        tier=Tier.TIER3,
        family=TaskFamily.BROWSER,
        surface="browser",
        user=SimulatedUser(turns=[UserTurn(message="Fix the page")]),
    )

    assert (
        classify_error_failure_mode(task, "page.evaluate: Invalid evaluate function")
        == FailureMode.STATE_REGRESSION
    )
    assert (
        classify_error_failure_mode(task, "could not connect to CDP: connection refused")
        == FailureMode.BROWSER_NAVIGATION_FAILURE
    )


def test_classify_delivery_outcome_supports_partial_credit():
    task = TaskDefinition(
        id="test-task",
        name="Test Task",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        user=SimulatedUser(turns=[UserTurn(message="Fix it")]),
    )

    assert (
        classify_delivery_outcome(
            task=task,
            completion_result=CompletionResult(total_assertions=2, passed_assertions=2, score=1.0),
            run_score=1.0,
        )
        == DeliveryOutcome.PASS
    )
    assert (
        classify_delivery_outcome(
            task=task,
            completion_result=CompletionResult(total_assertions=2, passed_assertions=1, score=0.5),
            run_score=0.5,
        )
        == DeliveryOutcome.PARTIAL
    )
    assert (
        classify_delivery_outcome(
            task=task,
            completion_result=CompletionResult(total_assertions=2, passed_assertions=0, score=0.0),
            run_score=0.0,
        )
        == DeliveryOutcome.FAIL
    )
