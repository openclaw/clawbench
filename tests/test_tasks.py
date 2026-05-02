from pathlib import Path

import clawbench.tasks as tasks_module
from clawbench.client import GatewayConfig
from clawbench.harness import BenchmarkHarness
from clawbench.tasks import load_all_tasks

PUBLIC_TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks-public"
tasks_module.TASKS_DIR = PUBLIC_TASKS_DIR


def test_load_all_tasks_returns_full_corpus():
    tasks = load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR)

    assert len(tasks) == 19
    assert {task.tier.value for task in tasks} == {"tier1", "tier2", "tier3", "tier4", "tier5"}
    assert any(task.capabilities for task in tasks)
    assert any(task.scenario is not None for task in tasks)
    assert any("ambiguous" in [variant.value for variant in task.prompt_variants] for task in tasks)
    assert sum(1 for task in tasks if task.judge is not None) >= 5
    assert all(task.pool.value == "public_dev" for task in tasks)
    assert all(task.setup.asset_packs for task in tasks)


def test_public_tasks_match_core_v1_manifest_shape():
    tasks = load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR)
    task_ids = {task.id for task in tasks}

    assert len(tasks) == 19
    assert "t1-bugfix-discount" in task_ids
    assert "t5-hallucination-resistant-evidence" in task_ids
    assert sum(1 for task in tasks if task.tier.value == "tier4") == 5
    assert sum(1 for task in tasks if task.family.value == "browser") == 2
    assert any("memory_continuation" in [cap.value for cap in task.capabilities] for task in tasks)


def test_load_all_tasks_supports_pool_subset_and_capability_filters():
    bugfix_tasks = load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR, capabilities=["bugfix"])
    coding_scene_tasks = load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR, scenario="coding_dev_assist")
    ambiguous_tasks = load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR, prompt_variant="ambiguous")

    assert bugfix_tasks
    assert coding_scene_tasks
    assert ambiguous_tasks
    assert all("bugfix" in [capability.value for capability in task.capabilities] for task in bugfix_tasks)
    assert all(task.scenario and task.scenario.value == "coding_dev_assist" for task in coding_scene_tasks)
    assert all("ambiguous" in [variant.value for variant in task.prompt_variants] for task in ambiguous_tasks)


def test_workspace_setup_preserves_nested_asset_paths(tmp_path: Path):
    # Use a task from the Core v1 public set (tasks-public/) so this test
    # passes whether the dev has private tasks/ or only the public release.
    # t4-browser-research-and-code has both flat files (report_client.py,
    # serve_docs.py) and nested dirs (docs/, tests/).
    task = next(
        task for task in load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR)
        if task.id == "t4-browser-research-and-code"
    )
    harness = BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        randomize_order=False,
        tasks_dir=PUBLIC_TASKS_DIR,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    harness._setup_workspace(task, workspace)

    assert (workspace / "report_client.py").exists()
    assert (workspace / "docs" / "index.html").exists()
    assert (workspace / "tests" / "test_report_client.py").exists()


def test_selected_tasks_include_judge_rubrics():
    # All assertions use task IDs from the Core v1 public set so CI
    # (without the private tasks/) reproduces locally.
    tasks = {task.id: task for task in load_all_tasks(tasks_dir=PUBLIC_TASKS_DIR)}

    assert tasks["t1-bugfix-discount"].judge is not None
    assert tasks["t3-feature-export"].judge is not None
    assert tasks["t4-browser-research-and-code"].judge is not None
    assert tasks["t4-delegation-repair"].judge is not None
    assert tasks["t5-hallucination-resistant-evidence"].judge is not None
