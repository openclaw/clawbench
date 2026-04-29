import datetime
import json

import pytest

from clawbench.hub import (
    dataset_has_submission_results,
    ensure_dataset_repo,
    load_submission_rows_from_parquet,
    resolve_dataset_repo,
    submission_parquet_files,
)
from clawbench.queue import Job, JobQueue, JobStatus, SubmissionRequest
import clawbench.queue as queue_module


def test_submission_request_defaults_to_single_parallel_lane():
    request = SubmissionRequest(model="openai-codex/gpt-5.4")

    assert request.max_parallel_lanes == 1
    assert request.runs_per_task == 3
    assert request.judge_affects_score is False


def test_submission_request_fingerprint_includes_judge_score_gate():
    advisory = SubmissionRequest(model="anthropic/claude-sonnet-4-6", judge_model="judge")
    weighted = SubmissionRequest(
        model="anthropic/claude-sonnet-4-6",
        judge_model="judge",
        judge_affects_score=True,
    )

    assert advisory.active_fingerprint() != weighted.active_fingerprint()


def test_save_local_replaces_queue_file_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_module, "LOCAL_QUEUE_DIR", tmp_path)
    monkeypatch.setattr(queue_module, "HF_TOKEN", "")
    queue = JobQueue()
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.PENDING,
            submitted_at="2026-04-09T00:00:01+00:00",
            request=SubmissionRequest(model="anthropic/claude-sonnet-4-6"),
        )
    }

    queue._save_local()

    jobs_file = tmp_path / "jobs.json"
    assert jobs_file.exists()
    assert Job(**json.loads(jobs_file.read_text(encoding="utf-8"))[0]).job_id == "job-1"
    assert not list(tmp_path.glob("jobs.*.tmp"))


@pytest.mark.asyncio
async def test_submit_dedupes_equivalent_active_jobs(monkeypatch):
    queue = JobQueue()
    request = SubmissionRequest(model="anthropic/claude-sonnet-4-6", submitter="vincent")
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.PENDING,
            submitted_at="2026-04-09T00:00:01+00:00",
            request=request,
        )
    }
    monkeypatch.setattr(queue, "_save_local", lambda: (_ for _ in ()).throw(AssertionError("should not save")))

    async def fail_sync() -> None:
        raise AssertionError("should not sync")

    monkeypatch.setattr(queue, "_sync_to_hub", fail_sync)

    job = await queue.submit(SubmissionRequest(model="anthropic/claude-sonnet-4-6", submitter="someone-else"))

    assert job.job_id == "job-1"


@pytest.mark.asyncio
async def test_submit_enforces_queue_capacity(monkeypatch):
    monkeypatch.setenv("CLAWBENCH_MAX_ACTIVE_QUEUE_JOBS", "1")
    queue = JobQueue()
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.EVALUATING,
            submitted_at="2026-04-09T00:00:01+00:00",
            request=SubmissionRequest(model="anthropic/claude-sonnet-4-6"),
        )
    }

    with pytest.raises(ValueError, match="Queue is at capacity"):
        await queue.submit(SubmissionRequest(model="huggingface/Qwen/Qwen3-32B"))


@pytest.mark.asyncio
async def test_submit_enforces_submitter_limit(monkeypatch):
    monkeypatch.setenv("CLAWBENCH_MAX_ACTIVE_JOBS_PER_SUBMITTER", "1")
    queue = JobQueue()
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.PENDING,
            submitted_at="2026-04-09T00:00:01+00:00",
            request=SubmissionRequest(model="anthropic/claude-sonnet-4-6", submitter="Vincent"),
        )
    }

    with pytest.raises(ValueError, match="already has 1 active"):
        await queue.submit(SubmissionRequest(model="huggingface/Qwen/Qwen3-32B", submitter=" vincent "))


@pytest.mark.asyncio
async def test_submit_enforces_per_submission_runtime_caps(monkeypatch):
    monkeypatch.setenv("CLAWBENCH_MAX_RUNS_PER_SUBMISSION", "2")
    monkeypatch.setenv("CLAWBENCH_MAX_LANES_PER_SUBMISSION", "1")
    queue = JobQueue()
    queue._jobs = {}

    with pytest.raises(ValueError, match="runs_per_task=3"):
        await queue.submit(SubmissionRequest(model="anthropic/claude-sonnet-4-6", runs_per_task=3))

    with pytest.raises(ValueError, match="max_parallel_lanes=2"):
        await queue.submit(
            SubmissionRequest(
                model="anthropic/claude-sonnet-4-6",
                runs_per_task=2,
                max_parallel_lanes=2,
            )
        )


def test_resolve_dataset_repo_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("CLAWBENCH_QUEUE_DATASET", "custom-owner/custom-results")
    monkeypatch.setenv("SPACE_ID", "ScoootScooob/clawbench")

    assert resolve_dataset_repo() == "custom-owner/custom-results"


def test_resolve_dataset_repo_derives_owner_from_space_id(monkeypatch):
    monkeypatch.delenv("CLAWBENCH_QUEUE_DATASET", raising=False)
    monkeypatch.setenv("SPACE_ID", "ScoootScooob/clawbench")

    assert resolve_dataset_repo() == "ScoootScooob/clawbench-results"


def test_ensure_dataset_repo_creates_missing_repo_and_bootstrap_readme():
    class FakeApi:
        def __init__(self) -> None:
            self.created: list[tuple[str, str, bool, bool]] = []
            self.uploaded: list[tuple[str, str, str]] = []

        def repo_info(self, repo_id: str, repo_type: str) -> None:
            raise RuntimeError("missing")

        def create_repo(self, repo_id: str, repo_type: str, private: bool, exist_ok: bool) -> None:
            self.created.append((repo_id, repo_type, private, exist_ok))

        def upload_file(
            self,
            *,
            path_or_fileobj: bytes,
            path_in_repo: str,
            repo_id: str,
            repo_type: str,
        ) -> None:
            assert b"ClawBench Results" in path_or_fileobj
            self.uploaded.append((path_in_repo, repo_id, repo_type))

    api = FakeApi()

    ensure_dataset_repo(api, "ScoootScooob/clawbench-results")

    assert api.created == [("ScoootScooob/clawbench-results", "dataset", False, True)]
    assert api.uploaded == [("README.md", "ScoootScooob/clawbench-results", "dataset")]


def test_dataset_has_submission_results_ignores_queue_only_files():
    class FakeApi:
        def list_repo_files(self, repo_id: str, repo_type: str) -> list[str]:
            assert repo_id == "ScoootScooob/clawbench-results"
            assert repo_type == "dataset"
            return ["README.md", "queue/jobs.json"]

    assert not dataset_has_submission_results(FakeApi(), "ScoootScooob/clawbench-results")


def test_dataset_has_submission_results_detects_uploaded_parquet():
    class FakeApi:
        def list_repo_files(self, repo_id: str, repo_type: str) -> list[str]:
            assert repo_id == "ScoootScooob/clawbench-results"
            assert repo_type == "dataset"
            return ["README.md", "data/submissions-00000-of-00001.parquet"]

    assert dataset_has_submission_results(FakeApi(), "ScoootScooob/clawbench-results")


def test_submission_parquet_files_filters_to_submissions_split():
    class FakeApi:
        def list_repo_files(self, repo_id: str, repo_type: str) -> list[str]:
            assert repo_id == "ScoootScooob/clawbench-results"
            assert repo_type == "dataset"
            return [
                "README.md",
                "queue/jobs.json",
                "data/submissions-00000-of-00001.parquet",
                "data/requests-00000-of-00001.parquet",
                "data/submissions-00001-of-00002.parquet",
            ]

    assert submission_parquet_files(FakeApi(), "ScoootScooob/clawbench-results") == [
        "data/submissions-00000-of-00001.parquet",
        "data/submissions-00001-of-00002.parquet",
    ]


def test_load_submission_rows_from_parquet_uses_direct_hub_download(tmp_path):
    class FakeApi:
        def list_repo_files(self, repo_id: str, repo_type: str) -> list[str]:
            assert repo_id == "ScoootScooob/clawbench-results"
            assert repo_type == "dataset"
            return ["data/submissions-00000-of-00001.parquet"]

    parquet_path = tmp_path / "submissions.parquet"
    download_calls: list[tuple[str, str, str, str | None]] = []

    def fake_downloader(*, repo_id: str, repo_type: str, filename: str, token: str | None = None) -> str:
        download_calls.append((repo_id, repo_type, filename, token))
        return str(parquet_path)

    class FakeFrame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [{"model": "anthropic/claude-sonnet-4-6", "overall_score": 0.7}]

    class FakePandas:
        @staticmethod
        def read_parquet(path):
            assert str(path) == str(parquet_path)
            return FakeFrame()

    rows = load_submission_rows_from_parquet(
        "ScoootScooob/clawbench-results",
        token="hf_test",
        api=FakeApi(),
        downloader=fake_downloader,
        pandas_module=FakePandas(),
    )

    assert download_calls == [
        ("ScoootScooob/clawbench-results", "dataset", "data/submissions-00000-of-00001.parquet", "hf_test")
    ]
    assert rows == [{"model": "anthropic/claude-sonnet-4-6", "overall_score": 0.7}]


@pytest.mark.asyncio
async def test_mark_evaluating_syncs_to_hub(monkeypatch):
    queue = JobQueue()
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.PENDING,
            request=SubmissionRequest(model="anthropic/claude-sonnet-4-6"),
        )
    }
    save_calls: list[str] = []
    sync_calls: list[str] = []

    def fake_save_local() -> None:
        save_calls.append("saved")

    async def fake_sync() -> None:
        sync_calls.append("synced")

    monkeypatch.setattr(queue, "_save_local", fake_save_local)
    monkeypatch.setattr(queue, "_sync_to_hub", fake_sync)

    await queue.mark_evaluating("job-1")

    assert queue._jobs["job-1"].status == JobStatus.EVALUATING
    assert queue._jobs["job-1"].started_at is not None
    assert save_calls == ["saved"]
    assert sync_calls == ["synced"]


@pytest.mark.asyncio
async def test_claim_pending_marks_multiple_jobs_evaluating(monkeypatch):
    queue = JobQueue()
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.PENDING,
            submitted_at="2026-04-09T00:00:01+00:00",
            request=SubmissionRequest(model="anthropic/claude-sonnet-4-6"),
        ),
        "job-2": Job(
            job_id="job-2",
            status=JobStatus.PENDING,
            submitted_at="2026-04-09T00:00:02+00:00",
            request=SubmissionRequest(model="huggingface/Qwen/Qwen3-32B"),
        ),
        "job-3": Job(
            job_id="job-3",
            status=JobStatus.FINISHED,
            submitted_at="2026-04-09T00:00:03+00:00",
            request=SubmissionRequest(model="huggingface/zai-org/GLM-5"),
        ),
    }
    save_calls: list[str] = []
    sync_calls: list[str] = []

    def fake_save_local() -> None:
        save_calls.append("saved")

    async def fake_sync() -> None:
        sync_calls.append("synced")

    monkeypatch.setattr(queue, "_save_local", fake_save_local)
    monkeypatch.setattr(queue, "_sync_to_hub", fake_sync)

    claimed = await queue.claim_pending(limit=2)

    assert [job.job_id for job in claimed] == ["job-1", "job-2"]
    assert queue._jobs["job-1"].status == JobStatus.EVALUATING
    assert queue._jobs["job-2"].status == JobStatus.EVALUATING
    assert queue._jobs["job-3"].status == JobStatus.FINISHED
    assert queue._jobs["job-1"].started_at is not None
    assert queue._jobs["job-2"].started_at is not None
    assert save_calls == ["saved"]
    assert sync_calls == ["synced"]


@pytest.mark.asyncio
async def test_update_progress_tracks_current_task_and_heartbeat(monkeypatch):
    queue = JobQueue()
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.EVALUATING,
            started_at="2026-04-09T00:00:00+00:00",
            request=SubmissionRequest(model="anthropic/claude-sonnet-4-6"),
        )
    }
    save_calls: list[str] = []
    sync_calls: list[str] = []

    def fake_save_local() -> None:
        save_calls.append("saved")

    async def fake_sync() -> None:
        sync_calls.append("synced")

    monkeypatch.setattr(queue, "_save_local", fake_save_local)
    monkeypatch.setattr(queue, "_sync_to_hub", fake_sync)

    await queue.update_progress(
        "job-1",
        current_task_id="t3-monitoring-automation",
        current_run_index=2,
        current_run_total=3,
        progress_message="Running t3-monitoring-automation (run 2/3)",
    )

    job = queue._jobs["job-1"]
    assert job.current_task_id == "t3-monitoring-automation"
    assert job.current_run_index == 2
    assert job.current_run_total == 3
    assert job.progress_message == "Running t3-monitoring-automation (run 2/3)"
    assert job.last_progress_at is not None
    assert save_calls == ["saved"]
    assert sync_calls == ["synced"]


@pytest.mark.asyncio
async def test_reclaim_stale_jobs_requeues_only_expired_evaluations(monkeypatch):
    queue = JobQueue()
    stale_started_at = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
    fresh_started_at = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat()
    queue._jobs = {
        "job-1": Job(
            job_id="job-1",
            status=JobStatus.EVALUATING,
            started_at=stale_started_at,
            last_progress_at=stale_started_at,
            current_task_id="t1-architecture-brief",
            current_run_index=1,
            current_run_total=3,
            progress_message="Running t1-architecture-brief (run 1/3)",
            request=SubmissionRequest(model="anthropic/claude-sonnet-4-6"),
        ),
        "job-2": Job(
            job_id="job-2",
            status=JobStatus.EVALUATING,
            started_at=fresh_started_at,
            last_progress_at=fresh_started_at,
            current_task_id="t1-bugfix-discount",
            current_run_index=1,
            current_run_total=3,
            progress_message="Running t1-bugfix-discount (run 1/3)",
            request=SubmissionRequest(model="huggingface/Qwen/Qwen3-32B"),
        ),
    }
    save_calls: list[str] = []
    sync_calls: list[str] = []

    def fake_save_local() -> None:
        save_calls.append("saved")

    async def fake_sync() -> None:
        sync_calls.append("synced")

    monkeypatch.setattr(queue, "_save_local", fake_save_local)
    monkeypatch.setattr(queue, "_sync_to_hub", fake_sync)

    reclaimed = await queue.reclaim_stale_jobs(stale_after_seconds=300)

    assert [job.job_id for job in reclaimed] == ["job-1"]
    stale_job = queue._jobs["job-1"]
    assert stale_job.status == JobStatus.PENDING
    assert stale_job.started_at is None
    assert stale_job.current_task_id is None
    assert stale_job.current_run_index is None
    assert stale_job.current_run_total is None
    assert stale_job.stale_requeues == 1
    assert stale_job.progress_message is not None
    assert stale_job.progress_message.startswith("Auto-requeued after stale evaluation lease")

    fresh_job = queue._jobs["job-2"]
    assert fresh_job.status == JobStatus.EVALUATING
    assert fresh_job.current_task_id == "t1-bugfix-discount"
    assert save_calls == ["saved"]
    assert sync_calls == ["synced"]
