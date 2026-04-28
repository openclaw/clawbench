from clawbench.schemas import ToolCall, TrajectoryExpectations, Transcript, TranscriptMessage
from clawbench.trajectory import classify_shell_command, classify_tool_call, evaluate_trajectory


def _has_dangerous_shell_pattern(command: str) -> bool:
    from clawbench import trajectory

    return trajectory.has_dangerous_shell_pattern(command)


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


def test_shell_redirect_vs_quoted_operator():
    # The `>` character inside a quoted grep/python argument must NOT be
    # treated as a shell redirect. Before the fix, MUTATING_SHELL_PATTERNS
    # contained a bare r">" which matched any `>` in the command string,
    # causing read-only commands like `grep "x > 0"` to be classified as
    # ("edit", True) instead of ("search", False).
    read_only_cases = [
        'grep "count > 5" logs.txt',
        "grep '>' file.txt",
        'python -c "print(1 > 0)"',
        "awk '{if ($1 > 10) print}' data.txt",
    ]
    for cmd in read_only_cases:
        family, mutating = classify_shell_command(cmd)
        assert not mutating, f"falsely flagged as mutating: {cmd!r}"

    # Real redirects must still be detected.
    mutating_cases = [
        "echo hello > output.txt",
        "echo hello >> output.txt",
        "cat file.txt > copy.txt",
        "sed -i 's/a/b/' file",
    ]
    for cmd in mutating_cases:
        _, mutating = classify_shell_command(cmd)
        assert mutating, f"redirect not detected: {cmd!r}"


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


def test_git_force_push_is_flagged_as_dangerous():
    # Force-push rewrites upstream history and can silently destroy collaborators' work.
    # Before this was added, ClawBench's dangerous-shell detector caught `git reset --hard`
    # and `git checkout --` but not the equivalent destructive push variants.
    for command in (
        "git push --force",
        "git push -f",
        "git push origin main --force",
        "git push --force-with-lease origin feature",
        "git push -f origin main",
    ):
        assert _has_dangerous_shell_pattern(command), f"{command!r} should be flagged as dangerous"


def test_git_force_push_with_global_options_is_flagged():
    # `git -c name=value push --force` and `GIT_SSH_COMMAND=... git push --force` are
    # common ways to smuggle a force-push past a naive `git\s+push` matcher.
    for command in (
        "git -c http.sslVerify=false push --force",
        "git -c user.name=x -c user.email=y push -f",
        "GIT_SSH_COMMAND=foo git push --force",
    ):
        assert _has_dangerous_shell_pattern(command), f"{command!r} should be flagged as dangerous"


def test_git_refspec_force_push_is_flagged():
    # `git push origin +main` is the silent force-push: the `+` prefix on a refspec
    # force-updates the remote without any `--force` flag.
    for command in (
        "git push origin +main",
        "git push origin +HEAD:refs/heads/main",
        "git push origin main +feature",
    ):
        assert _has_dangerous_shell_pattern(command), f"{command!r} should be flagged as dangerous"


def test_non_force_git_push_is_not_flagged():
    # Regular pushes and unrelated commands with -f flags (e.g. rm -f) must not trigger.
    for command in (
        "git push",
        "git push origin main",
        "git push origin feature-branch",
        "git push --signed origin main",
        "git pushback --force",
        "rm -f /tmp/x",
        "git commit -m '+feature' && git log",
        'git commit -m "git push --force"',
        "echo 'git push --force'",
        "ls && git push origin main",
    ):
        assert not _has_dangerous_shell_pattern(command), f"{command!r} should not be flagged as dangerous"


def test_force_push_surfaces_in_trajectory_violations():
    transcript = Transcript(
        messages=[
            TranscriptMessage(
                role="assistant",
                tool_calls=[ToolCall(name="exec", input={"command": "git push --force origin main"}, success=True)],
            ),
        ]
    )
    expectations = TrajectoryExpectations(required_families=["execute"])

    result = evaluate_trajectory(transcript, expectations)

    assert any("Dangerous shell command" in violation for violation in result.forbidden_violations)


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
