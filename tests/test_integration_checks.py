import shutil
import subprocess
from pathlib import Path

import pytest

import clawbench.tasks as tasks_module
from clawbench.client import GatewayConfig
from clawbench.environment_files import (
    EVALUATOR_WORKSPACE_RUNTIME_KEY,
    PROTECTED_WORKSPACE_HASHES_RUNTIME_KEY,
)
from clawbench.environment import verify_completion
from clawbench.harness import BenchmarkHarness
from clawbench.schemas import ToolCall, Transcript, TranscriptMessage
from clawbench.services import build_runtime_values, start_background_services, stop_background_services
from clawbench.tasks import load_all_tasks
from clawbench.trajectory import evaluate_trajectory

PUBLIC_TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks-public"
tasks_module.TASKS_DIR = PUBLIC_TASKS_DIR


class DummyClient:
    async def _rpc(self, *args, **kwargs):  # pragma: no cover - should not be used in these checks
        raise AssertionError("This test path should not hit gateway RPCs")


def _prepare_workspace(task_id: str, tmp_path: Path) -> tuple[Path, object]:
    task = next(task for task in load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR) if task.id == task_id)
    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        randomize_order=False,
        tasks_dir=PUBLIC_TASKS_DIR,
    )
    workspace = tmp_path / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    harness._setup_workspace(task, workspace)
    return workspace, task


def _prepare_isolated_workspace(
    task_id: str,
    tmp_path: Path,
) -> tuple[Path, Path, object, dict[str, str]]:
    task = next(task for task in load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR) if task.id == task_id)
    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        randomize_order=False,
        tasks_dir=PUBLIC_TASKS_DIR,
    )
    workspace = tmp_path / f"{task_id}-workspace"
    evaluator = tmp_path / f"{task_id}-evaluator"
    workspace.mkdir(parents=True, exist_ok=True)
    evaluator.mkdir(parents=True, exist_ok=True)
    protected_hashes = harness._setup_workspace(task, workspace, evaluator_workspace=evaluator)
    return workspace, evaluator, task, protected_hashes


def _isolated_runtime_values(
    workspace: Path,
    evaluator: Path,
    protected_hashes: dict[str, str],
):
    return {
        **build_runtime_values(workspace=workspace, repo_root=Path.cwd()),
        EVALUATOR_WORKSPACE_RUNTIME_KEY: str(evaluator),
        PROTECTED_WORKSPACE_HASHES_RUNTIME_KEY: protected_hashes,
    }


@pytest.mark.asyncio
async def test_python_completion_check_passes_after_fix(tmp_path: Path):
    workspace, task = _prepare_workspace("t1-bugfix-discount", tmp_path)
    (workspace / "pricing.py").write_text(
        "def apply_discount(subtotal_cents: int, discount_percent: int) -> int:\n"
        "    discount_amount = subtotal_cents * discount_percent // 100\n"
        "    return subtotal_cents - discount_amount\n",
        encoding="utf-8",
    )

    runtime_values = build_runtime_values(workspace=workspace, repo_root=Path.cwd())
    result = await verify_completion(
        task.completion,
        workspace=workspace,
        client=DummyClient(),  # type: ignore[arg-type]
        session_key="",
        runtime_values=runtime_values,
    )

    assert result.score == 1.0

def _playwright_available() -> bool:
    if not shutil.which("node"):
        return False
    probe = subprocess.run(
        ["node", "-e", "require('playwright')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    return probe.returncode == 0


@pytest.mark.asyncio
async def test_browser_completion_check_passes_after_fix(tmp_path: Path):
    if not _playwright_available():
        pytest.skip("playwright is not installed in the local node runtime")

    workspace, task = _prepare_workspace("t2-browser-form-fix", tmp_path)
    (workspace / "app.js").write_text(
        "const form = document.getElementById('contact-form');\n"
        "const emailInput = document.getElementById('email');\n"
        "const statusNode = document.getElementById('status');\n\n"
        "form.addEventListener('submit', (event) => {\n"
        "  event.preventDefault();\n"
        "  const email = emailInput.value.trim();\n"
        "  if (!email.includes('@')) {\n"
        "    statusNode.textContent = 'Enter a valid email.';\n"
        "    return;\n"
        "  }\n"
        "  statusNode.textContent = `Saved ${email}`;\n"
        "});\n",
        encoding="utf-8",
    )
    runtime_values = build_runtime_values(workspace=workspace, repo_root=Path.cwd())
    services, runtime_values = await start_background_services(
        task.setup.background_services,
        workspace=workspace,
        repo_root=Path.cwd(),
        runtime_values=runtime_values,
    )
    try:
        result = await verify_completion(
            task.completion,
            workspace=workspace,
            client=DummyClient(),  # type: ignore[arg-type]
            session_key="",
            runtime_values=runtime_values,
        )
        assert result.score == 1.0
    finally:
        await stop_background_services(services)


@pytest.mark.asyncio
async def test_workspace_verifier_overwrite_does_not_pass(tmp_path: Path):
    workspace, evaluator, task, protected_hashes = _prepare_isolated_workspace(
        "t3-data-sql-query",
        tmp_path,
    )
    (workspace / "verify_results.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    runtime_values = _isolated_runtime_values(workspace, evaluator, protected_hashes)

    result = await verify_completion(
        task.completion,
        workspace=workspace,
        client=DummyClient(),  # type: ignore[arg-type]
        session_key="",
        runtime_values=runtime_values,
    )

    assert result.score == 0.0
    assert result.execution_results[0].command.startswith("python3 ")
    assert str(evaluator / "verify_results.py") in result.execution_results[0].command


@pytest.mark.asyncio
async def test_workspace_expected_output_overwrite_does_not_pass(tmp_path: Path):
    workspace, evaluator, task, protected_hashes = _prepare_isolated_workspace(
        "t3-data-pipeline-report",
        tmp_path,
    )
    (workspace / "expected").mkdir(exist_ok=True)
    (workspace / "expected" / "report.txt").write_text("", encoding="utf-8")
    (workspace / "pipeline.py").write_text("print('')\n", encoding="utf-8")
    runtime_values = _isolated_runtime_values(workspace, evaluator, protected_hashes)

    result = await verify_completion(
        task.completion,
        workspace=workspace,
        client=DummyClient(),  # type: ignore[arg-type]
        session_key="",
        runtime_values=runtime_values,
    )

    assert result.score == 0.0
    assert result.failed_assertions


@pytest.mark.asyncio
async def test_workspace_test_mutation_is_detected(tmp_path: Path):
    workspace, evaluator, task, protected_hashes = _prepare_isolated_workspace(
        "t1-bugfix-discount",
        tmp_path,
    )
    (workspace / "tests" / "test_pricing.py").write_text(
        "def test_green(): assert True\n",
        encoding="utf-8",
    )
    runtime_values = _isolated_runtime_values(workspace, evaluator, protected_hashes)

    result = await verify_completion(
        task.completion,
        workspace=workspace,
        client=DummyClient(),  # type: ignore[arg-type]
        session_key="",
        runtime_values=runtime_values,
    )

    assert result.score == 0.0
    assert "Protected evaluator asset modified" in result.failed_assertions[0]


def test_memory_task_trajectory_requires_memory_tool():
    task = next(
        task for task in load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR)
        if task.id == "t4-memory-recall-continuation"
    )
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "cat docs/release_notes.md"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="memory_store", input={"key": "beta rollout regions"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="write_file", input={"path": "flags.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=True)]),
        ]
    )

    result = evaluate_trajectory(transcript, task.trajectory)
    assert result.required_families_missing == []
    assert result.score > 0.7


def test_delegation_task_trajectory_requires_delegate_family():
    task = next(
        task for task in load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR)
        if task.id == "t4-delegation-repair"
    )
    transcript = Transcript(
        messages=[
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "rg billing ."}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "cat notifications.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="delegate_task", input={"task": "fix notifications"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="write_file", input={"path": "billing.py"}, success=True)]),
            TranscriptMessage(role="assistant", tool_calls=[ToolCall(name="exec", input={"command": "pytest -q"}, success=True)]),
        ]
    )

    result = evaluate_trajectory(transcript, task.trajectory)
    assert result.required_families_missing == []
    assert result.score > 0.7
