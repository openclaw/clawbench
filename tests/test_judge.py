from pathlib import Path

import pytest

from clawbench.judge import build_judge_prompt, parse_judge_response
from clawbench.schemas import (
    CompletionResult,
    JudgeExpectations,
    SimulatedUser,
    TaskDefinition,
    TaskFamily,
    Tier,
    ToolCall,
    Transcript,
    TranscriptMessage,
    UserTurn,
)


def _make_task(judge: JudgeExpectations) -> TaskDefinition:
    return TaskDefinition(
        id="judge-task",
        name="Judge Task",
        tier=Tier.TIER5,
        family=TaskFamily.ADVERSARIAL,
        surface="coding",
        user=SimulatedUser(turns=[UserTurn(message="Do the thing")]),
        judge=judge,
    )


def test_build_judge_prompt_includes_artifacts_completion_feedback_and_transcript(tmp_path: Path):
    (tmp_path / "answer.txt").write_text("Support window: 18 months\n", encoding="utf-8")
    judge = JudgeExpectations(
        rubric="Check that the answer is grounded and auditable.",
        artifact_paths=["answer.txt"],
        include_transcript=True,
        include_completion_feedback=True,
    )
    task = _make_task(judge)
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="user", text="Use the local docs only."),
            TranscriptMessage(
                role="assistant",
                text="First I'll inspect the docs, then I'll write the answer.",
                tool_calls=[ToolCall(name="read_file", family="read", success=True)],
            ),
        ]
    )
    completion_result = CompletionResult(
        total_assertions=2,
        passed_assertions=1,
        failed_assertions=["EXEC python3 verify_answer.py: wrong quote"],
        score=0.5,
    )

    prompt = build_judge_prompt(
        task=task,
        judge=judge,
        transcript=transcript,
        workspace=tmp_path,
        completion_result=completion_result,
    )

    assert "Judge threshold: 0.70" in prompt
    assert "=== answer.txt ===" in prompt
    assert "Support window: 18 months" in prompt
    assert "completion assertions: 1/2" in prompt
    assert "wrong quote" in prompt
    assert "tool families: read x1" in prompt


def test_build_judge_prompt_rejects_artifact_paths_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "outside-judge.txt"
    outside.write_text("do not leak", encoding="utf-8")
    judge = JudgeExpectations(
        rubric="Check that the answer is grounded and auditable.",
        artifact_paths=["../outside-judge.txt"],
    )
    task = _make_task(judge)

    prompt = build_judge_prompt(
        task=task,
        judge=judge,
        transcript=Transcript(),
        workspace=tmp_path,
        completion_result=CompletionResult(score=1.0),
    )

    assert "invalid path" in prompt
    assert "do not leak" not in prompt


def test_parse_judge_response_accepts_wrapped_json_and_computes_pass():
    result = parse_judge_response(
        'Score summary:\n{"score": 0.82, "confidence": 0.66, "reason": "Strong evidence.", "rubric_hits": ["grounded"], "rubric_misses": []}',
        passing_threshold=0.8,
    )

    assert result.enabled is True
    assert result.score == 0.82
    assert result.confidence == 0.66
    assert result.passed is True
    assert result.rubric_hits == ["grounded"]
    assert result.error is None


def test_parse_judge_response_reports_invalid_json():
    result = parse_judge_response("not json at all", passing_threshold=0.7)

    assert result.enabled is True
    assert result.error == "Judge response did not contain valid JSON."
    assert result.passed is False


def test_parse_judge_response_falls_back_to_labeled_text():
    result = parse_judge_response(
        """
score: 0.84
confidence: 0.72
reason: Grounded in the artifact and easy to audit.
rubric_hits:
- cites the right file
- keeps the answer precise
rubric_misses:
- could quote a shorter line
""",
        passing_threshold=0.8,
    )

    assert result.score == 0.84
    assert result.confidence == 0.72
    assert result.passed is True
    assert result.rubric_hits == ["cites the right file", "keeps the answer precise"]


@pytest.mark.asyncio
async def test_judge_task_run_uses_gateway_session_and_parses_response(tmp_path: Path):
    from clawbench.judge import judge_task_run

    (tmp_path / "answer.txt").write_text("Support window: 18 months\n", encoding="utf-8")
    judge = JudgeExpectations(
        rubric="Check whether the answer is grounded.",
        artifact_paths=["answer.txt"],
    )
    task = _make_task(judge)
    transcript = Transcript(messages=[TranscriptMessage(role="assistant", text="I checked the docs.")])

    class FakeClient:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def create_session(self, *, model: str, label: str) -> str:
            assert model == "judge-model"
            assert label.startswith("clawbench-judge-")
            return "judge-session-1"

        async def subscribe(self, session_key: str) -> None:
            assert session_key == "judge-session-1"

        async def send_and_wait(self, session_key: str, message: str):
            assert session_key == "judge-session-1"
            assert "Support window: 18 months" in message
            return Transcript(
                messages=[
                    TranscriptMessage(
                        role="assistant",
                        text='{"score": 0.91, "confidence": 0.75, "reason": "Grounded.", "rubric_hits": ["exact"], "rubric_misses": []}',
                    )
                ]
            )

        async def delete_session(self, session_key: str) -> None:
            self.deleted.append(session_key)

    client = FakeClient()
    result = await judge_task_run(
        task=task,
        transcript=transcript,
        workspace=tmp_path,
        client=client,  # type: ignore[arg-type]
        judge_model="judge-model",
        completion_result=CompletionResult(total_assertions=1, passed_assertions=1, score=1.0),
    )

    assert result.enabled is True
    assert result.model == "judge-model"
    assert result.score == 0.91
    assert result.passed is True
    assert client.deleted == ["judge-session-1"]
