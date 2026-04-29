import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clawbench.queue import Job, JobQueue, JobStatus, SubmissionRequest
from clawbench.worker import GATEWAY_PORT, GATEWAY_PORT_SPACING, EvalWorker, JobProgressTracker, ParallelLane


class DummyTask:
    def __init__(
        self,
        task_id: str,
        tier: str,
        family: str,
        phases: int = 1,
        capabilities: list[str] | None = None,
    ) -> None:
        self.id = task_id
        self.tier = SimpleNamespace(value=tier)
        self.family = SimpleNamespace(value=family)
        self._phases = phases
        self.capabilities = [SimpleNamespace(value=value) for value in (capabilities or [])]

    def normalized_phases(self):
        return [object()] * self._phases


class FakeQueue:
    def __init__(self) -> None:
        self.evaluating: list[str] = []
        self.finished: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []
        self.progress: list[tuple[str, dict[str, object]]] = []

    async def mark_evaluating(self, job_id: str) -> None:
        self.evaluating.append(job_id)

    async def mark_finished(self, job_id: str, result_id: str) -> None:
        self.finished.append((job_id, result_id))

    async def mark_failed(self, job_id: str, error: str) -> None:
        self.failed.append((job_id, error))

    async def update_progress(self, job_id: str, **kwargs) -> None:
        self.progress.append((job_id, kwargs))


class FakeBenchmarkResult:
    submission_id = "submission-1"
    overall_score = 0.82
    overall_pass_hat_k = 1.0

    def model_dump(self):
        return {
            "submission_id": self.submission_id,
            "overall_score": self.overall_score,
            "overall_pass_hat_k": self.overall_pass_hat_k,
        }


def make_job(*, status: JobStatus = JobStatus.PENDING, lanes: int = 1) -> Job:
    return Job(
        job_id="job-1",
        status=status,
        request=SubmissionRequest(
            model="anthropic/claude-sonnet-4-6",
            provider="anthropic",
            runs_per_task=1,
            max_parallel_lanes=lanes,
        ),
    )


def test_configure_browser_runtime_sets_benchmark_safe_openclaw_config(monkeypatch):
    worker = EvalWorker(JobQueue())
    state_dir = Path("/tmp/test-openclaw-config-basic")
    if state_dir.exists():
        import shutil

        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    config_path = state_dir / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))

    worker._configure_browser_runtime(["node", "/openclaw/dist/cli.js"], {"HOME": "/tmp/home"})

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "agents": {"defaults": {"skipBootstrap": True}},
        "browser": {"headless": True, "noSandbox": True},
    }


def test_configure_browser_runtime_pins_subagents_to_active_model(monkeypatch):
    worker = EvalWorker(JobQueue())
    worker.set_active_model("openai-codex/gpt-5.4")
    state_dir = Path("/tmp/test-openclaw-config-model")
    if state_dir.exists():
        import shutil

        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    config_path = state_dir / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))

    worker._configure_browser_runtime(["node", "/openclaw/dist/cli.js"], {"HOME": "/tmp/home"})

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "agents": {
            "defaults": {
                "skipBootstrap": True,
                "model": {"primary": "openai-codex/gpt-5.4"},
                "subagents": {"model": {"primary": "openai-codex/gpt-5.4"}},
            }
        },
        "browser": {"headless": True, "noSandbox": True},
    }


@pytest.mark.asyncio
async def test_prepare_benchmark_run_restarts_gateway_on_task_boundary(monkeypatch):
    worker = EvalWorker(JobQueue())
    calls: list[str] = []

    def fake_stop_gateway() -> None:
        calls.append("stop")

    async def fake_ensure_gateway() -> None:
        calls.append("ensure")

    monkeypatch.setattr(worker, "_stop_gateway", fake_stop_gateway)
    monkeypatch.setattr(worker, "_ensure_gateway", fake_ensure_gateway)

    task = DummyTask("t1-bugfix-discount", "tier1", "coding")

    await worker._prepare_benchmark_run(task, 0)
    await worker._prepare_benchmark_run(task, 1)
    await worker._prepare_benchmark_run(DummyTask("t1-refactor-csv-loader", "tier1", "coding"), 0)

    assert calls == ["stop", "ensure"]


@pytest.mark.asyncio
async def test_prepare_benchmark_run_restarts_each_run_for_automation(monkeypatch):
    worker = EvalWorker(JobQueue())
    calls: list[str] = []

    def fake_stop_gateway() -> None:
        calls.append("stop")

    async def fake_ensure_gateway() -> None:
        calls.append("ensure")

    monkeypatch.setattr(worker, "_stop_gateway", fake_stop_gateway)
    monkeypatch.setattr(worker, "_ensure_gateway", fake_ensure_gateway)

    task = DummyTask(
        "t3-monitoring-automation",
        "tier3",
        "tools",
        capabilities=["automation"],
    )

    await worker._prepare_benchmark_run(task, 0)
    await worker._prepare_benchmark_run(task, 1)

    assert calls == ["stop", "ensure"]


def test_plan_parallel_lanes_serializes_browser_tasks():
    worker = EvalWorker(JobQueue())
    tasks = [
        DummyTask("t1", "tier1", "coding"),
        DummyTask("t2", "tier4", "browser"),
        DummyTask("t3", "tier3", "repo"),
        DummyTask("t4", "tier2", "browser"),
        DummyTask("t5", "tier5", "multi_tool", phases=2),
    ]

    lanes = worker._plan_parallel_lanes(tasks, requested_parallel_lanes=3)

    assert len(lanes) == 3
    browser_lanes = [lane for lane in lanes if lane.browser_lane]
    assert len(browser_lanes) == 1
    assert [task.id for task in browser_lanes[0].tasks] == ["t2", "t4"]
    assert all(
        task.family.value != "browser"
        for lane in lanes
        if not lane.browser_lane
        for task in lane.tasks
    )


def test_materialize_lane_runtime_spaces_ports_and_copies_auth(tmp_path: Path, monkeypatch):
    source_state = tmp_path / "source-state"
    auth_path = source_state / "agents" / "main" / "agent"
    auth_path.mkdir(parents=True, exist_ok=True)
    (auth_path / "auth-profiles.json").write_text('{"default": "ok"}', encoding="utf-8")
    (source_state / "openclaw.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(source_state))

    worker = EvalWorker(JobQueue())
    lane0 = ParallelLane(index=0, tasks=[DummyTask("t1", "tier1", "coding")])
    lane1 = ParallelLane(index=1, tasks=[DummyTask("t2", "tier2", "browser")], browser_lane=True)

    job_root = tmp_path / "job-root"
    worker._materialize_lane_runtime(lane0, job_root)
    worker._materialize_lane_runtime(lane1, job_root)

    assert lane0.port == GATEWAY_PORT
    assert lane1.port == GATEWAY_PORT + GATEWAY_PORT_SPACING
    assert lane1.state_dir is not None
    assert (lane1.state_dir / "agents" / "main" / "agent" / "auth-profiles.json").exists()


@pytest.mark.asyncio
async def test_process_job_finishes_when_optional_result_upload_fails(tmp_path: Path, monkeypatch):
    queue = FakeQueue()
    worker = EvalWorker(queue)  # type: ignore[arg-type]
    cleanup_calls: list[str] = []

    async def fake_run_serial_benchmark(job, tasks, progress):  # noqa: ANN001
        progress.mark_serial(tasks[0].id, 0, stage="running")
        return FakeBenchmarkResult()

    async def fake_upload_result(result):  # noqa: ANN001
        raise RuntimeError("hub upload unavailable")

    monkeypatch.setattr("clawbench.worker.RESULTS_DIR", tmp_path)
    monkeypatch.setattr(worker, "_load_job_tasks", lambda job: [DummyTask("t1", "tier1", "coding")])
    monkeypatch.setattr(worker, "_run_serial_benchmark", fake_run_serial_benchmark)
    monkeypatch.setattr(worker, "_stop_gateway", lambda: cleanup_calls.append("serial"))
    monkeypatch.setattr(worker, "_stop_parallel_gateways", lambda: cleanup_calls.append("parallel"))
    monkeypatch.setattr("clawbench.upload.upload_result", fake_upload_result)

    await worker._process_job(make_job())

    assert queue.evaluating == ["job-1"]
    assert queue.finished == [("job-1", "submission-1")]
    assert queue.failed == []
    assert (tmp_path / "submission-1.json").exists()
    assert cleanup_calls[-2:] == ["serial", "parallel"]
    assert worker._active_model == ""
    assert worker._serial_last_task_id is None


@pytest.mark.asyncio
async def test_process_job_marks_failure_and_cleans_up_after_benchmark_error(monkeypatch):
    queue = FakeQueue()
    worker = EvalWorker(queue)  # type: ignore[arg-type]
    cleanup_calls: list[str] = []

    async def fail_run_serial_benchmark(job, tasks, progress):  # noqa: ANN001
        raise RuntimeError("gateway died")

    monkeypatch.setattr(worker, "_load_job_tasks", lambda job: [DummyTask("t1", "tier1", "coding")])
    monkeypatch.setattr(worker, "_run_serial_benchmark", fail_run_serial_benchmark)
    monkeypatch.setattr(worker, "_stop_gateway", lambda: cleanup_calls.append("serial"))
    monkeypatch.setattr(worker, "_stop_parallel_gateways", lambda: cleanup_calls.append("parallel"))

    await worker._process_job(make_job())

    assert queue.evaluating == ["job-1"]
    assert queue.finished == []
    assert queue.failed == [("job-1", "gateway died")]
    assert cleanup_calls[-2:] == ["serial", "parallel"]
    assert worker._active_model == ""
    assert worker._serial_last_task_id is None


@pytest.mark.asyncio
async def test_process_job_does_not_reclaim_already_claimed_evaluating_job(tmp_path: Path, monkeypatch):
    queue = FakeQueue()
    worker = EvalWorker(queue)  # type: ignore[arg-type]

    async def fake_run_serial_benchmark(job, tasks, progress):  # noqa: ANN001
        return FakeBenchmarkResult()

    async def fake_upload_result(result):  # noqa: ANN001
        return None

    monkeypatch.setattr("clawbench.worker.RESULTS_DIR", tmp_path)
    monkeypatch.setattr(worker, "_load_job_tasks", lambda job: [DummyTask("t1", "tier1", "coding")])
    monkeypatch.setattr(worker, "_run_serial_benchmark", fake_run_serial_benchmark)
    monkeypatch.setattr(worker, "_stop_gateway", lambda: None)
    monkeypatch.setattr(worker, "_stop_parallel_gateways", lambda: None)
    monkeypatch.setattr("clawbench.upload.upload_result", fake_upload_result)

    await worker._process_job(make_job(status=JobStatus.EVALUATING))

    assert queue.evaluating == []
    assert queue.finished == [("job-1", "submission-1")]


@pytest.mark.asyncio
async def test_run_serial_benchmark_forwards_judge_score_gate(monkeypatch):
    queue = JobQueue()
    worker = EvalWorker(queue)
    captured: dict[str, object] = {}

    async def fake_ensure_gateway() -> None:
        return None

    async def fake_preflight_browser_support_for_tasks(*args, **kwargs) -> None:
        return None

    class FakeHarness:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            return SimpleNamespace(submission_id="submission-1")

    monkeypatch.setattr(worker, "_stop_gateway", lambda: None)
    monkeypatch.setattr(worker, "_ensure_gateway", fake_ensure_gateway)
    monkeypatch.setattr(worker, "_preflight_browser_support_for_tasks", fake_preflight_browser_support_for_tasks)
    monkeypatch.setattr("clawbench.worker.BenchmarkHarness", FakeHarness)

    job = SimpleNamespace(
        request=SimpleNamespace(
            model="anthropic/claude-sonnet-4-6",
            provider="anthropic",
            judge_model="judge-model",
            judge_affects_score=True,
            runs_per_task=1,
            tier="tier1",
            scenario=None,
            prompt_variant="clear",
        )
    )
    progress = JobProgressTracker(total_tasks=1, runs_per_task=1, requested_parallel_lanes=1)

    await worker._run_serial_benchmark(
        job,
        [DummyTask("t1-bugfix-discount", "tier1", "coding")],
        progress,
    )

    assert captured["judge_model"] == "judge-model"
    assert captured["judge_affects_score"] is True


@pytest.mark.asyncio
async def test_ensure_gateway_closes_parent_log_handle(monkeypatch):
    worker = EvalWorker(JobQueue())
    captured_handles = []

    class FakeProcess:
        returncode = None
        pid = 123456

        def poll(self):
            return None

    def fake_popen(*args, stdout=None, **kwargs):
        captured_handles.append(stdout)
        return FakeProcess()

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    async def fake_assert_gateway_control_plane(config):
        return None

    monkeypatch.setattr(worker, "_find_gateway_cmd", lambda: ["node", "/fake/openclaw.js"])
    monkeypatch.setattr(worker, "_configure_browser_runtime", lambda gateway_cmd, gateway_env: None)
    monkeypatch.setattr("clawbench.worker.subprocess.Popen", fake_popen)
    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(worker, "_assert_gateway_control_plane", fake_assert_gateway_control_plane)

    await worker._ensure_gateway()

    assert captured_handles
    assert captured_handles[0].closed


def test_job_progress_tracker_drops_finished_parallel_lane():
    tracker = JobProgressTracker(total_tasks=20, runs_per_task=3, requested_parallel_lanes=2)

    tracker.mark_lane(0, "t4-delegation-repair", 0, stage="running")
    tracker.mark_lane(1, "t4-browser-research-and-code", 1, stage="running")
    snapshot = tracker.clear_lane(0)

    assert snapshot == {
        "current_task_id": "t4-browser-research-and-code",
        "current_run_index": 2,
        "current_run_total": 3,
        "progress_message": "L2 running t4-browser-research-and-code (run 2/3)",
    }


@pytest.mark.asyncio
async def test_run_job_heartbeat_flushes_latest_progress_snapshot(monkeypatch):
    queue = JobQueue()
    worker = EvalWorker(queue)
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_update_progress(job_id: str, **kwargs) -> None:
        calls.append((job_id, kwargs))

    monkeypatch.setattr(queue, "update_progress", fake_update_progress)

    tracker = JobProgressTracker(total_tasks=20, runs_per_task=3, requested_parallel_lanes=1)
    tracker.mark_serial("t1-bugfix-discount", 1, stage="running")
    stop_event = asyncio.Event()
    stop_event.set()

    await worker._run_job_heartbeat("job-1", tracker, stop_event)

    assert calls == [
        (
            "job-1",
            {
                "current_task_id": "t1-bugfix-discount",
                "current_run_index": 2,
                "current_run_total": 3,
                "progress_message": "Running t1-bugfix-discount (run 2/3)",
            },
        )
    ]
