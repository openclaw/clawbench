from clawbench.schemas import ToolCall, TrajectoryExpectations, Transcript, TranscriptMessage
from clawbench.trajectory import classify_tool_call, evaluate_trajectory


def test_trajectory_rewards_read_before_write_and_self_verification():
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "rg TODO ."}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="write_file", input={"path": "foo.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=True)]),
        ]
    )
    expectations = TrajectoryExpectations(
        required_families=["search", "edit", "execute"],
        required_pre_edit_families=["search"],
        required_post_edit_families=["execute"],
        min_distinct_families=3,
        min_pre_edit_exploration_calls=1,
        min_post_edit_verification_calls=1,
        require_read_before_mutation=True,
        require_self_verification=True,
    )

    result = evaluate_trajectory(transcript, expectations)

    assert result.score > 0.8
    assert result.read_before_write_ratio == 1.0
    assert result.self_verified is True
    assert result.required_families_missing == []


def test_trajectory_penalizes_missing_successful_delegation():
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="read_file", input={"path": "billing.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="write_file", input={"path": "billing.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=True)]),
        ]
    )
    expectations = TrajectoryExpectations(
        required_families=["read", "edit", "execute", "delegate"],
        required_pre_edit_families=["read"],
        required_post_edit_families=["execute", "delegate"],
        min_distinct_families=4,
        min_successful_delegations=1,
        require_read_before_mutation=True,
        require_self_verification=True,
    )

    result = evaluate_trajectory(transcript, expectations)

    assert "delegate" in result.required_families_missing
    assert result.tool_fit_score == 0.0
    assert result.score < 0.6


def test_trajectory_tracks_recovery_and_dangerous_commands():
    transcript = Transcript(
        messages=[
            TranscriptMessage(
                role="assistant",
                tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=False, output="ERROR failed test")],
            ),
            TranscriptMessage(
                role="assistant",
                tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=False, output="ERROR failed test")],
            ),
            TranscriptMessage(
                role="assistant",
                tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=True, output="2 passed")],
            ),
            TranscriptMessage(
                role="assistant",
                tool_calls=[ToolCall(name="exec", input={"command": "rm -rf build"}, success=True)],
            ),
        ]
    )
    expectations = TrajectoryExpectations(
        required_families=["execute"],
        expect_recovery=True,
        max_recovery_turns=3,
    )

    result = evaluate_trajectory(transcript, expectations)

    assert result.recovered_failures == 2
    assert result.repeated_failures >= 1
    assert any("Dangerous shell command" in violation for violation in result.forbidden_violations)


def test_trajectory_counts_distinct_read_and_mutation_targets():
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="read_file", input={"path": "src/app.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="read_file", input={"path": "tests/test_app.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="write_file", input={"path": "src/app.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="write_file", input={"path": "src/helpers.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=True)]),
        ]
    )
    expectations = TrajectoryExpectations(
        required_families=["read", "edit", "execute"],
        min_distinct_families=3,
        min_distinct_read_targets_pre_edit=2,
        min_distinct_mutation_targets=2,
        require_read_before_mutation=True,
        require_self_verification=True,
    )

    result = evaluate_trajectory(transcript, expectations)

    assert result.distinct_read_targets_pre_edit == ["src/app.py", "tests/test_app.py"]
    assert result.distinct_mutation_targets == ["src/app.py", "src/helpers.py"]
    assert result.score > 0.8


def test_replace_and_insert_tools_are_classified_as_edit():
    # str_replace and insert_text are common in-place mutation tools used by many agents.
    # Both were previously falling through all checks and returning ("unknown", False),
    # and search-first matching also misclassified find_replace/search_replace as search.
    for tool_name in (
        "str_replace",
        "replace_in_file",
        "insert_text",
        "insert_at_line",
        "find_replace",
        "search_replace",
    ):
        tool_call = ToolCall(name=tool_name, input={"path": "foo.py"}, success=True)
        family, mutating = classify_tool_call(tool_call)
        assert family == "edit", f"{tool_name!r} classified as {family!r}, expected 'edit'"
        assert mutating is True, f"{tool_name!r} classified as non-mutating"


def test_str_replace_mutation_is_detected_in_trajectory():
    # When an agent edits via str_replace, the trajectory scorer must detect the mutation.
    # Before the fix, str_replace was classified as ("unknown", False): zero mutations were
    # detected, so read_before_write_ratio was 1.0 for the wrong reason and the edit family
    # never appeared in distinct_families.
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="read_file", input={"path": "src/calc.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="str_replace", input={"path": "src/calc.py", "old_str": "return x", "new_str": "return x + 1"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=True)]),
        ]
    )
    expectations = TrajectoryExpectations(
        required_families=["read", "edit", "execute"],
        require_read_before_mutation=True,
        require_self_verification=True,
        min_distinct_mutation_targets=1,
    )

    result = evaluate_trajectory(transcript, expectations)

    assert "edit" not in result.required_families_missing
    assert result.distinct_mutation_targets == ["src/calc.py"]
    assert result.self_verified is True
    assert result.read_before_write_ratio == 1.0


def test_find_replace_mutation_is_not_misclassified_as_search():
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="read_file", input={"path": "src/calc.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="find_replace", input={"path": "src/calc.py", "find": "return x", "replace": "return x + 1"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=True)]),
        ]
    )
    expectations = TrajectoryExpectations(
        required_families=["read", "edit", "execute"],
        require_read_before_mutation=True,
        require_self_verification=True,
        min_distinct_mutation_targets=1,
    )

    result = evaluate_trajectory(transcript, expectations)

    assert "edit" not in result.required_families_missing
    assert "search" not in result.distinct_families
    assert result.distinct_mutation_targets == ["src/calc.py"]


def test_memory_search_is_not_treated_as_a_mutation():
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="memory_search", input={"query": "release notes"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="write_file", input={"path": "handoff.md"}, success=True)]),
        ]
    )
    expectations = TrajectoryExpectations(
        required_families=["memory", "edit"],
        require_read_before_mutation=True,
    )

    result = evaluate_trajectory(transcript, expectations)

    assert result.read_before_write_ratio == 1.0
