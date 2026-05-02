from pathlib import Path

import pytest

from clawbench.client import GatewayConfig
from clawbench.adapters.base import AdapterContext, AgentAdapter, PhaseResult, StateQueryResult
from clawbench.canonical import AdapterCapability, CanonicalPhase, StateQuery
from clawbench.harness import BenchmarkHarness
from clawbench.schemas import (
    CompletionResult,
    CompletionSpec,
    FileState,
    JudgeExpectations,
    JudgeResult,
    SimulatedUser,
    TaskDefinition,
    TaskFamily,
    TaskRunResult,
    Tier,
    Transcript,
    TranscriptMessage,
    UserTurn,
)
from clawbench.tasks import load_all_tasks


class FakeGatewayClient:
    def __init__(self) -> None:
        self.create_agent_calls: list[tuple[str, str]] = []

    async def create_agent(self, *, name: str, workspace: str) -> str:
        self.create_agent_calls.append((name, workspace))
        return "agent-test-123"


@pytest.mark.asyncio
async def test_run_agent_uses_staged_run_workspace(tmp_path: Path):
    task = next(task for task in load_all_tasks() if task.id == "t1-bugfix-discount")
    harness = BenchmarkHarness(gateway_config=GatewayConfig(), model="test-model", randomize_order=False)
    workspace = tmp_path / "run-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    client = FakeGatewayClient()

    agent_id = await harness._create_run_agent(
        client,  # type: ignore[arg-type]
        task=task,
        workspace=workspace,
        run_index=2,
    )

    assert agent_id == "agent-test-123"
    assert client.create_agent_calls == [(client.create_agent_calls[0][0], str(workspace))]
    assert task.id in client.create_agent_calls[0][0]


@pytest.mark.asyncio
async def test_prepare_run_hook_executes_before_each_run(monkeypatch):
    task = next(task for task in load_all_tasks() if task.id == "t1-bugfix-discount")
    calls: list[tuple[str, int]] = []

    async def prepare_run(current_task, run_index: int) -> None:
        calls.append((current_task.id, run_index))

    async def fake_run_single(self, current_task, run_index: int):
        from clawbench.schemas import TaskRunResult

        return TaskRunResult(
            task_id=current_task.id,
            tier=current_task.tier.value,
            family=current_task.family.value,
            run_index=run_index,
            run_score=1.0,
        )

    monkeypatch.setattr("clawbench.harness.load_all_tasks", lambda **_: [task])
    monkeypatch.setattr(BenchmarkHarness, "_run_single", fake_run_single)

    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        task_ids=[task.id],
        runs_per_task=2,
        randomize_order=False,
        prepare_run=prepare_run,
    )

    await harness.run()

    assert calls == [(task.id, 0), (task.id, 1)]


def test_aggregate_reports_advisory_judge_metrics():
    task = next(task for task in load_all_tasks() if task.id == "t5-hallucination-resistant-evidence")
    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        judge_model="judge-model",
        task_ids=[task.id],
        randomize_order=False,
    )
    runs = [
        TaskRunResult(
            task_id=task.id,
            tier=task.tier.value,
            family=task.family.value,
            run_index=0,
            run_score=0.9,
            completion_result=CompletionResult(total_assertions=1, passed_assertions=1, score=1.0),
            judge_result=JudgeResult(enabled=True, model="judge-model", score=0.9, confidence=0.7, passed=True),
        ),
        TaskRunResult(
            task_id=task.id,
            tier=task.tier.value,
            family=task.family.value,
            run_index=1,
            run_score=0.6,
            completion_result=CompletionResult(total_assertions=1, passed_assertions=1, score=1.0),
            judge_result=JudgeResult(enabled=True, model="judge-model", score=0.5, confidence=0.9, passed=False),
        ),
    ]

    result = harness._aggregate([task], {task.id: runs})
    task_result = result.task_results[0]

    assert result.judge_model == "judge-model"
    assert result.overall_judge_score == pytest.approx(0.7)
    assert result.overall_judge_confidence == pytest.approx(0.8)
    assert result.overall_judge_pass_rate == pytest.approx(0.5)
    assert result.judge_task_coverage == 1.0
    assert task_result.mean_judge_score == pytest.approx(0.7)
    assert task_result.mean_judge_confidence == pytest.approx(0.8)
    assert task_result.judge_pass_rate == pytest.approx(0.5)
    assert task_result.judged_runs == 2


def test_compose_result_from_task_stats_supports_parallel_environment_metadata():
    task = next(task for task in load_all_tasks() if task.id == "t1-bugfix-discount").model_copy(deep=True)
    task.category = "software_engineering"
    task.domain = "devtools"
    task.functionality = ["bugfix", "regression_repair", "test_verification"]
    task.trace_distribution = ["read_heavy", "edit_heavy", "execute_heavy", "recovery_heavy"]
    task.tool_surface = ["filesystem", "shell"]
    task.risk_tags = ["code_change"]
    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        task_ids=[task.id],
        randomize_order=False,
        print_report=False,
        quiet=True,
    )
    runs = [
        TaskRunResult(
            task_id=task.id,
            tier=task.tier.value,
            family=task.family.value,
            run_index=0,
            run_score=0.9,
            completion_result=CompletionResult(total_assertions=1, passed_assertions=1, score=1.0),
        ),
        TaskRunResult(
            task_id=task.id,
            tier=task.tier.value,
            family=task.family.value,
            run_index=1,
            run_score=0.7,
            completion_result=CompletionResult(total_assertions=1, passed_assertions=1, score=1.0),
        ),
    ]

    base_result = harness._aggregate([task], {task.id: runs})
    merged_result = harness.compose_result_from_task_stats(
        base_result.task_results,
        tasks=[task],
        environment_extra={
            "parallel_lanes": 2,
            "requested_parallel_lanes": 3,
            "browser_tasks_serialized": False,
        },
        print_report=False,
    )

    assert merged_result.overall_score == pytest.approx(base_result.overall_score)
    assert merged_result.overall_completion == pytest.approx(base_result.overall_completion)
    assert merged_result.environment["parallel_lanes"] == 2
    assert merged_result.environment["requested_parallel_lanes"] == 3
    assert merged_result.environment["browser_tasks_serialized"] is False
    assert merged_result.environment["dimension_coverage"] == {
        "category": 1,
        "domain": 1,
        "functionality": 3,
        "trace_distribution": 4,
        "tool_surface": 2,
        "risk_tag": 1,
    }
    assert merged_result.task_results[0].category == "software_engineering"
    assert merged_result.task_results[0].domain == "devtools"

    category = {item.value: item for item in merged_result.category_results}
    assert category["software_engineering"].task_ids == [task.id]
    assert category["software_engineering"].weighted_score == pytest.approx(
        base_result.overall_weighted_query_score
    )

    functionality_values = {item.value for item in merged_result.functionality_results}
    assert {"bugfix", "regression_repair", "test_verification"}.issubset(functionality_values)
    trace_values = {item.value for item in merged_result.trace_distribution_results}
    assert {"read_heavy", "edit_heavy", "execute_heavy", "recovery_heavy"}.issubset(trace_values)
    assert "category" in merged_result.dimension_results
    assert merged_result.dimension_results["category"] == merged_result.category_results


@pytest.mark.asyncio
async def test_run_records_adapter_surface(monkeypatch):
    task = next(task for task in load_all_tasks() if task.id == "t1-bugfix-discount")

    async def fake_run_single(self, current_task, run_index: int):
        return TaskRunResult(
            task_id=current_task.id,
            tier=current_task.tier.value,
            family=current_task.family.value,
            run_index=run_index,
            run_score=1.0,
            completion_result=CompletionResult(total_assertions=1, passed_assertions=1, score=1.0),
        )

    monkeypatch.setattr("clawbench.harness.load_all_tasks", lambda **_: [task])
    monkeypatch.setattr(BenchmarkHarness, "_run_single", fake_run_single)

    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        adapter="openclaw",
        runs_per_task=1,
        randomize_order=False,
        print_report=False,
        quiet=True,
    )

    result = await harness.run()

    assert result.environment["adapter"] == "openclaw"
    assert "hermes" in result.environment["known_adapters"]


@pytest.mark.asyncio
async def test_run_rejects_registered_but_unwired_adapter(monkeypatch):
    task = next(task for task in load_all_tasks() if task.id == "t1-bugfix-discount")
    monkeypatch.setattr("clawbench.harness.load_all_tasks", lambda **_: [task])

    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        adapter="codex",
        runs_per_task=1,
        randomize_order=False,
        print_report=False,
        quiet=True,
    )

    with pytest.raises(ValueError, match="not yet wired"):
        await harness.run()


def _files_only_definition(judge: JudgeExpectations | None = None) -> TaskDefinition:
    return TaskDefinition(
        id="adapter-files-only",
        name="Adapter files only",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        user=SimulatedUser(
            max_turns=1,
            turns=[UserTurn(message="Create answer.txt")],
        ),
        completion=CompletionSpec(
            files=[FileState(path="answer.txt", exists=True, content_contains=["done"])],
        ),
        judge=judge,
    )


class FakeAgentAdapter(AgentAdapter):
    name = "hermes"
    capabilities = {AdapterCapability.FILES, AdapterCapability.EXECUTION}

    async def setup(self, ctx: AdapterContext) -> None:
        return None

    async def run_phase(self, phase: CanonicalPhase, ctx: AdapterContext) -> PhaseResult:
        (ctx.workspace / "answer.txt").write_text("done\n", encoding="utf-8")
        message = TranscriptMessage(role="assistant", text="Created answer.txt and verified it.")
        ctx.transcript.messages.append(message)
        return PhaseResult(messages=[message], completed_normally=True)

    async def verify_state_query(self, query: StateQuery, ctx: AdapterContext) -> StateQueryResult:
        return StateQueryResult(ok=False, capability_missing=True)

    async def teardown(self, ctx: AdapterContext) -> None:
        return None


@pytest.mark.asyncio
async def test_hermes_adapter_runs_through_scoring_harness(monkeypatch, tmp_path: Path):
    task = _files_only_definition()
    monkeypatch.setattr("clawbench.harness.load_all_tasks", lambda **_: [task])
    monkeypatch.setattr("clawbench.harness.get_adapter", lambda name: FakeAgentAdapter)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAWBENCH_RUN_CACHE_DIR", "")

    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="openai/gpt-5.5",
        adapter="hermes",
        runs_per_task=1,
        randomize_order=False,
        print_report=False,
        quiet=True,
    )

    result = await harness.run()
    run = harness.last_task_runs[task.id][0]

    assert result.environment["adapter"] == "hermes"
    assert result.environment["executable_adapters"] == ["hermes", "openclaw"]
    assert run.error is None
    assert run.completion_result.score == 1.0
    assert run.delivery_outcome.value == "pass"


@pytest.mark.asyncio
async def test_openclaw_uses_shared_adapter_scoring_path(monkeypatch, tmp_path: Path):
    task = _files_only_definition()
    monkeypatch.setattr("clawbench.harness.load_all_tasks", lambda **_: [task])
    monkeypatch.setattr("clawbench.harness.get_adapter", lambda name: FakeAgentAdapter)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAWBENCH_RUN_CACHE_DIR", "")

    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="openai/gpt-5.5",
        adapter="openclaw",
        runs_per_task=1,
        randomize_order=False,
        print_report=False,
        quiet=True,
    )

    result = await harness.run()
    run = harness.last_task_runs[task.id][0]

    assert result.environment["adapter"] == "openclaw"
    assert run.error is None
    assert run.completion_result.score == 1.0
    assert run.delivery_outcome.value == "pass"


@pytest.mark.asyncio
async def test_adapter_scoring_uses_advisory_judge(monkeypatch, tmp_path: Path):
    task = _files_only_definition(
        JudgeExpectations(
            rubric="Reward the answer when it is concise.",
            artifact_paths=["answer.txt"],
            passing_threshold=0.4,
        )
    )
    monkeypatch.setattr("clawbench.harness.load_all_tasks", lambda **_: [task])
    monkeypatch.setattr("clawbench.harness.get_adapter", lambda name: FakeAgentAdapter)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAWBENCH_RUN_CACHE_DIR", "")

    class FakeJudgeGateway:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def create_session(self, *, model: str, label: str) -> str:
            assert model == "judge-model"
            assert label.startswith("clawbench-judge-")
            return "judge-session"

        async def subscribe(self, session_key: str) -> None:
            assert session_key == "judge-session"

        async def send_and_wait(self, session_key: str, message: str):
            assert session_key == "judge-session"
            assert "done" in message
            return Transcript(
                messages=[
                    TranscriptMessage(
                        role="assistant",
                        text='{"score": 0.5, "confidence": 0.8, "reason": "OK", "rubric_hits": [], "rubric_misses": []}',
                    )
                ]
            )

        async def delete_session(self, session_key: str) -> None:
            assert session_key == "judge-session"

    monkeypatch.setattr("clawbench.harness.GatewayClient", lambda config: FakeJudgeGateway())

    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="openai/gpt-5.5",
        adapter="hermes",
        judge_model="judge-model",
        runs_per_task=1,
        randomize_order=False,
        print_report=False,
        quiet=True,
    )

    result = await harness.run()
    run = harness.last_task_runs[task.id][0]

    assert run.judge_result.enabled is True
    assert run.judge_result.score == pytest.approx(0.5)
    assert run.run_score == pytest.approx(0.95)
    assert result.overall_judge_score == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_hermes_adapter_filters_incompatible_tasks(monkeypatch):
    task = next(task for task in load_all_tasks() if task.id == "t4-memory-recall-continuation")
    monkeypatch.setattr("clawbench.harness.load_all_tasks", lambda **_: [task])
    monkeypatch.setattr("clawbench.harness.get_adapter", lambda name: FakeAgentAdapter)

    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="openai/gpt-5.5",
        adapter="hermes",
        runs_per_task=1,
        randomize_order=False,
        print_report=False,
        quiet=True,
    )

    with pytest.raises(ValueError, match="No selected tasks are compatible"):
        await harness.run()
