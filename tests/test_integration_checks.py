import shutil
import subprocess
from pathlib import Path

import pytest

from clawbench.client import GatewayConfig
from clawbench.environment import verify_completion
from clawbench.harness import BenchmarkHarness
from clawbench.schemas import ToolCall, Transcript, TranscriptMessage
from clawbench.services import build_runtime_values, start_background_services, stop_background_services
from clawbench.tasks import load_all_tasks
from clawbench.trajectory import evaluate_trajectory

# The task set is moving to a private holdout; the public repo will ship a
# different task set soon. Until then, skip integration tests that need
# specific task ids when the tasks directory isn't present.
_TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks"
pytestmark = pytest.mark.skipif(
    not _TASKS_DIR.exists(),
    reason="tasks/ directory not present (private holdout — public set TBD)",
)


class DummyClient:
    async def _rpc(self, *args, **kwargs):  # pragma: no cover - should not be used in these checks
        raise AssertionError("This test path should not hit gateway RPCs")


def _prepare_workspace(task_id: str, tmp_path: Path) -> tuple[Path, object]:
    task = next(task for task in load_all_tasks() if task.id == task_id)
    harness = BenchmarkHarness(gateway_config=GatewayConfig(), model="test-model", randomize_order=False)
    workspace = tmp_path / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    harness._setup_workspace(task, workspace)
    return workspace, task


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


@pytest.mark.asyncio
async def test_node_completion_check_passes_after_fix(tmp_path: Path):
    workspace, task = _prepare_workspace("t2-node-search-patch", tmp_path)
    # After hardening, render.js also exports emptyNote() with a legitimate
    # empty body. The scoped fix only patches normalizeNote's body and must
    # leave emptyNote alone.
    (workspace / "src" / "render.js").write_text(
        "function normalizeNote(note) {\n"
        "  return {\n"
        "    title: note.title.trim(),\n"
        "    body: note.body.trim(),\n"
        "  };\n"
        "}\n\n"
        "function emptyNote() {\n"
        "  return {\n"
        "    title: \"\",\n"
        "    body: \"\",\n"
        "  };\n"
        "}\n\n"
        "module.exports = { normalizeNote, emptyNote };\n",
        encoding="utf-8",
    )
    (workspace / "src" / "search.js").write_text(
        "function filterNotes(notes, query) {\n"
        "  const needle = query.trim().toLowerCase();\n"
        "  return notes.filter((note) => note.title.toLowerCase().includes(needle) || note.body.toLowerCase().includes(needle));\n"
        "}\n\n"
        "module.exports = { filterNotes };\n",
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


def test_memory_task_trajectory_requires_memory_tool():
    task = next(task for task in load_all_tasks() if task.id == "t4-memory-recall-continuation")
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
    task = next(task for task in load_all_tasks() if task.id == "t4-delegation-repair")
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
