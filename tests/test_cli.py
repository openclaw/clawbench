from click.testing import CliRunner

from clawbench.cli import SCENARIO_CHOICES, cli
from clawbench.schemas import ScenarioDomain


def test_cli_scenario_choices_track_schema_enum():
    assert SCENARIO_CHOICES == [scenario.value for scenario in ScenarioDomain]


def test_run_command_forwards_judge_score_gate(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeResult:
        submission_id = "submission-1"

        def model_dump(self):
            return {"submission_id": self.submission_id}

    class FakeHarness:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            return FakeResult()

    monkeypatch.setattr("clawbench.cli.BenchmarkHarness", FakeHarness)

    output = tmp_path / "result.json"
    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--model",
            "anthropic/claude-sonnet-4-6",
            "--judge-model",
            "judge-model",
            "--judge-affects-score",
            "--runs",
            "1",
            "--task",
            "t1-bugfix-discount",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["judge_model"] == "judge-model"
    assert captured["judge_affects_score"] is True
    assert output.read_text(encoding="utf-8")
