import pytest

from clawbench.schemas import BenchmarkResult
from clawbench.upload import _json_column, _submission_shard_name, upload_result


def test_submission_shard_name_sanitizes_ids():
    assert _submission_shard_name("abc/def:ghi") == "abc-def-ghi.parquet"
    assert _submission_shard_name("...") == "submission.parquet"


@pytest.mark.asyncio
async def test_upload_result_writes_append_only_submission_shard(monkeypatch):
    uploads = []
    ensured = []
    uploaded_rows = []

    class FakeApi:
        def __init__(self, token: str) -> None:
            self.token = token

        def upload_file(self, *, path_or_fileobj: str, path_in_repo: str, repo_id: str, repo_type: str) -> None:
            import pandas as pd

            uploads.append((path_or_fileobj, path_in_repo, repo_id, repo_type))
            uploaded_rows.extend(pd.read_parquet(path_or_fileobj).to_dict(orient="records"))

    monkeypatch.setattr("huggingface_hub.HfApi", FakeApi)
    monkeypatch.setattr(
        "clawbench.upload.ensure_dataset_repo",
        lambda api, repo_id: ensured.append((api.token, repo_id)),
    )

    result = BenchmarkResult(
        submission_id="run/123",
        model="anthropic/claude-sonnet-4-6",
        provider="anthropic",
        timestamp="2026-04-28T00:00:00+00:00",
        overall_score=0.8,
        overall_completion=0.9,
        overall_trajectory=0.7,
        overall_behavior=0.8,
        overall_ci_lower=0.7,
        overall_ci_upper=0.9,
        overall_pass_hat_k=1.0,
    )

    url = await upload_result(result, dataset_repo="openclaw/clawbench-results", token="hf_test")

    assert url == "https://huggingface.co/datasets/openclaw/clawbench-results"
    assert ensured == [("hf_test", "openclaw/clawbench-results")]
    assert len(uploads) == 1
    local_path, path_in_repo, repo_id, repo_type = uploads[0]
    assert local_path.endswith("run-123.parquet")
    assert path_in_repo == "data/submissions/run-123.parquet"
    assert repo_id == "openclaw/clawbench-results"
    assert repo_type == "dataset"
    assert uploaded_rows[0]["overall_delivery_outcome_counts"] == "{}"
    assert uploaded_rows[0]["task_results"] == "[]"


def test_json_column_is_stable_and_compact():
    assert _json_column({"b": 2, "a": 1}) == '{"a":1,"b":2}'
