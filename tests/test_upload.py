import pytest

from clawbench.schemas import BenchmarkResult
from clawbench.upload import upload_result


def _result(submission_id: str = "run/123") -> BenchmarkResult:
    return BenchmarkResult(
        submission_id=submission_id,
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


@pytest.mark.asyncio
async def test_upload_result_requires_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="HF_TOKEN not set"):
        await upload_result(_result(), dataset_repo="openclaw/clawbench-results")


@pytest.mark.asyncio
async def test_upload_result_appends_and_deduplicates_submissions(monkeypatch):
    ensured = []
    pushed = []

    class FakeApi:
        def __init__(self, token: str) -> None:
            self.token = token

    class FakeDataset:
        def __init__(self, rows):
            self.rows = rows

        @classmethod
        def from_list(cls, rows):
            return cls(rows)

        def push_to_hub(self, repo_id: str, *, split: str, token: str) -> None:
            pushed.append((repo_id, split, token, self.rows))

    monkeypatch.setattr("huggingface_hub.HfApi", FakeApi)
    monkeypatch.setattr("datasets.Dataset", FakeDataset)
    monkeypatch.setattr(
        "datasets.load_dataset",
        lambda *args, **kwargs: [
            {"submission_id": "old-run", "model": "old-model"},
            {"submission_id": "run/123", "model": "stale-model"},
        ],
    )
    monkeypatch.setattr(
        "clawbench.upload.ensure_dataset_repo",
        lambda api, repo_id: ensured.append((api.token, repo_id)),
    )

    url = await upload_result(_result(), dataset_repo="openclaw/clawbench-results", token="hf_test")

    assert url == "https://huggingface.co/datasets/openclaw/clawbench-results"
    assert ensured == [("hf_test", "openclaw/clawbench-results")]
    assert len(pushed) == 1

    repo_id, split, token, rows = pushed[0]
    assert repo_id == "openclaw/clawbench-results"
    assert split == "submissions"
    assert token == "hf_test"
    assert [row["submission_id"] for row in rows] == ["old-run", "run/123"]
    assert rows[-1]["model"] == "anthropic/claude-sonnet-4-6"
