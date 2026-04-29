from __future__ import annotations

import datetime
import importlib
import json
import sys
import threading
from pathlib import Path

import pytest

from clawbench.client import GatewayConfig
from clawbench.harness import BenchmarkHarness
from clawbench.queue import Job, JobQueue, JobStatus, SubmissionRequest
import clawbench.queue as queue_module
from clawbench.schemas import (
    CompletionSpec,
    ExecutionCheck,
    SimulatedUser,
    TaskDefinition,
    TaskFamily,
    Tier,
    ToolCall,
    TrajectoryExpectations,
    Transcript,
    TranscriptMessage,
    UserTurn,
)
from clawbench.worker import EvalWorker


def _runtime_task() -> TaskDefinition:
    return TaskDefinition(
        id="runtime-contract-smoke",
        name="Runtime Contract Smoke",
        tier=Tier.TIER1,
        family=TaskFamily.TOOLS,
        surface="tools",
        user=SimulatedUser(
            max_turns=1,
            turns=[UserTurn(message="create answer.txt with runtime ok, then verify it")],
        ),
        completion=CompletionSpec(
            execution_checks=[
                ExecutionCheck(
                    name="answer artifact",
                    command=(
                        "{python_exe} -c "
                        "\"from pathlib import Path; "
                        "assert Path('answer.txt').read_text(encoding='utf-8') == 'runtime ok\\n'\""
                    ),
                )
            ]
        ),
        trajectory=TrajectoryExpectations(
            required_families=["read", "edit", "execute"],
            min_distinct_families=3,
            require_read_before_mutation=True,
            require_self_verification=True,
        ),
    )


class _GatewayState:
    def __init__(self) -> None:
        self.agent_workspaces: dict[str, Path] = {}
        self.session_agents: dict[str, str] = {}
        self.deleted_sessions: list[str] = []
        self.deleted_agents: list[str] = []


class _SuccessfulGatewayClient:
    state = _GatewayState()

    def __init__(self, config: GatewayConfig | None = None) -> None:
        self.config = config or GatewayConfig()

    async def __aenter__(self) -> _SuccessfulGatewayClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def create_agent(self, *, name: str, workspace: str) -> str:
        agent_id = f"agent-{len(self.state.agent_workspaces) + 1}"
        self.state.agent_workspaces[agent_id] = Path(workspace)
        return agent_id

    async def create_session(self, *, model: str, agent_id: str, label: str) -> str:  # noqa: ARG002
        session_key = f"session-{len(self.state.session_agents) + 1}"
        self.state.session_agents[session_key] = agent_id
        return session_key

    async def subscribe(self, session_key: str) -> None:  # noqa: ARG002
        return None

    async def send_and_wait(self, session_key: str, message: str, *, timeout: float) -> Transcript:  # noqa: ARG002
        workspace = self.state.agent_workspaces[self.state.session_agents[session_key]]
        (workspace / "answer.txt").write_text("runtime ok\n", encoding="utf-8")
        return Transcript(
            messages=[
                TranscriptMessage(
                    role="assistant",
                    text="i'll inspect, write the answer, then verify it.",
                    tool_calls=[
                        ToolCall(
                            name="read_file",
                            input={"path": "answer.txt"},
                            output="missing",
                            success=True,
                        ),
                        ToolCall(
                            name="write_file",
                            input={"path": "answer.txt"},
                            output="wrote answer.txt",
                            success=True,
                        ),
                        ToolCall(
                            name="shell",
                            input={"command": "python -m pytest -q"},
                            output="1 passed",
                            success=True,
                        ),
                    ],
                ),
                TranscriptMessage(role="assistant", text="done, verified."),
            ]
        )

    async def delete_session(self, session_key: str) -> None:
        self.state.deleted_sessions.append(session_key)

    async def delete_agent(self, agent_id: str, *, delete_files: bool = False) -> None:  # noqa: ARG002
        self.state.deleted_agents.append(agent_id)


class _DisconnectingGatewayClient(_SuccessfulGatewayClient):
    async def send_and_wait(self, session_key: str, message: str, *, timeout: float) -> Transcript:  # noqa: ARG002
        raise ConnectionError("gateway connection dropped")


@pytest.mark.asyncio
async def test_queue_worker_harness_scorer_happy_path_writes_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    queue_dir = tmp_path / "queue"
    results_dir = tmp_path / "results"
    state_dir = tmp_path / "state"
    monkeypatch.setattr(queue_module, "LOCAL_QUEUE_DIR", queue_dir)
    monkeypatch.setattr(queue_module, "HF_TOKEN", "")
    monkeypatch.setattr("clawbench.worker.RESULTS_DIR", results_dir)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    monkeypatch.setenv("CLAWBENCH_RUN_CACHE_DIR", str(tmp_path / "run-cache"))
    monkeypatch.setattr("clawbench.harness.GatewayClient", _SuccessfulGatewayClient)

    async def fake_upload_result(result) -> None:  # noqa: ANN001
        return None

    async def fake_ensure_gateway() -> None:
        return None

    async def fake_preflight_browser_support_for_tasks(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    task = _runtime_task()
    queue = JobQueue()
    job = await queue.submit(
        SubmissionRequest(
            model="test/model",
            provider="test",
            runs_per_task=1,
            max_parallel_lanes=1,
        )
    )
    claimed = await queue.claim_pending()
    assert [claimed_job.job_id for claimed_job in claimed] == [job.job_id]

    worker = EvalWorker(queue)
    monkeypatch.setattr(worker, "_load_job_tasks", lambda current_job: [task])
    monkeypatch.setattr("clawbench.harness.load_all_tasks", lambda **kwargs: [task])
    monkeypatch.setattr(worker, "_ensure_gateway", fake_ensure_gateway)
    monkeypatch.setattr(worker, "_preflight_browser_support_for_tasks", fake_preflight_browser_support_for_tasks)
    monkeypatch.setattr(worker, "_stop_gateway", lambda: None)
    monkeypatch.setattr(worker, "_stop_parallel_gateways", lambda: None)
    monkeypatch.setattr("clawbench.upload.upload_result", fake_upload_result)

    await worker._process_job(claimed[0])

    finished = await queue.get_status(job.job_id)
    assert finished is not None
    assert finished.status == JobStatus.FINISHED
    assert finished.result_id is not None
    assert finished.progress_message == "Finished"
    result_path = results_dir / f"{finished.result_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["model"] == "test/model"
    assert result["overall_completion"] == 1.0
    assert result["overall_pass_hat_k"] == 1.0
    assert result["task_results"][0]["task_id"] == "runtime-contract-smoke"


@pytest.mark.asyncio
async def test_harness_turn_disconnect_becomes_failed_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAWBENCH_RUN_CACHE_DIR", str(tmp_path / "run-cache"))
    monkeypatch.setattr("clawbench.harness.GatewayClient", _DisconnectingGatewayClient)

    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test/model",
        randomize_order=False,
        print_report=False,
        quiet=True,
    )

    result = await harness._run_single(_runtime_task(), 0)

    assert result.run_score == 0.0
    assert result.delivery_outcome.value == "fail"
    assert result.failure_mode is not None
    assert result.failure_mode.value == "environment_unavailable"
    assert "gateway connection dropped" in (result.error or "")


@pytest.mark.asyncio
async def test_harness_scorer_exception_becomes_failed_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAWBENCH_RUN_CACHE_DIR", str(tmp_path / "run-cache"))
    monkeypatch.setattr("clawbench.harness.GatewayClient", _SuccessfulGatewayClient)

    async def fail_score_task_run(**kwargs):  # noqa: ANN003
        raise RuntimeError("scorer exploded")

    monkeypatch.setattr("clawbench.harness.score_task_run", fail_score_task_run)
    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test/model",
        randomize_order=False,
        print_report=False,
        quiet=True,
    )

    result = await harness._run_single(_runtime_task(), 0)

    assert result.run_score == 0.0
    assert result.delivery_outcome.value == "fail"
    assert result.failure_mode is not None
    assert result.failure_mode.value == "state_regression"
    assert result.error == "scorer exploded"


@pytest.mark.asyncio
async def test_stale_evaluating_job_can_be_reclaimed_and_claimed_again(monkeypatch: pytest.MonkeyPatch):
    queue = JobQueue()
    stale_started_at = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    ).isoformat()
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.EVALUATING,
            started_at=stale_started_at,
            last_progress_at=stale_started_at,
            current_task_id="runtime-contract-smoke",
            current_run_index=1,
            current_run_total=1,
            attempt_count=1,
            request=SubmissionRequest(model="test/model"),
        )
    }
    monkeypatch.setattr(queue, "_save_local", lambda: None)

    async def fake_sync_to_hub() -> None:
        return None

    monkeypatch.setattr(queue, "_sync_to_hub", fake_sync_to_hub)

    reclaimed = await queue.reclaim_stale_jobs(stale_after_seconds=300)
    claimed = await queue.claim_pending()

    assert [job.job_id for job in reclaimed] == ["job-1"]
    assert [job.job_id for job in claimed] == ["job-1"]
    job = queue._jobs["job-1"]
    assert job.status == JobStatus.EVALUATING
    assert job.attempt_count == 2
    assert job.stale_requeues == 1
    assert job.current_task_id is None
    assert job.current_run_index is None
    assert job.progress_message == "Queued for evaluation"


def test_leaderboard_skips_malformed_local_result_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class NoopThread:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

        def start(self) -> None:
            return None

    monkeypatch.setattr(threading, "Thread", NoopThread)
    monkeypatch.setattr(queue_module, "LOCAL_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(queue_module, "HF_TOKEN", "")
    sys.modules.pop("app", None)
    app = importlib.import_module("app")

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "bad.json").write_text("{not json", encoding="utf-8")
    (results_dir / "good.json").write_text(
        json.dumps(
            {
                "model": "test/model",
                "timestamp": "2026-04-29T00:00:00+00:00",
                "overall_score": 0.91,
                "overall_completion": 1.0,
                "overall_trajectory": 0.8,
                "overall_behavior": 1.0,
                "overall_pass_hat_k": 1.0,
                "environment": {"prompt_variant": "clear", "scenario": "all"},
                "task_results": [{"task_id": "runtime-contract-smoke"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(app, "dataset_has_submission_results", lambda api, repo: False)

    frame = app._load_leaderboard_uncached()

    assert list(frame["Model"]) == ["test/model"]
    assert list(frame["Score"]) == [0.91]
