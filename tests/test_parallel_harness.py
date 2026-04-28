"""Unit tests for the parallel harness execution path.

These do not need the OpenClaw gateway. They monkey-patch _run_single
with a deterministic stub and verify:

  1. Concurrency=1 reproduces the old serial behavior exactly.
  2. Concurrency>1 actually runs work items in parallel (overlap detected).
  3. Browser tasks are serialized via browser_concurrency=1 even when the
     global concurrency is higher.
  4. Per-task results are aggregated in the correct order.
  5. Speedup at concurrency=N matches the theoretical wall time.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawbench.client import GatewayConfig
from clawbench.harness import BenchmarkHarness
from clawbench.schemas import (
    BehaviorResult,
    CompletionResult,
    DeliveryOutcome,
    EfficiencyResult,
    JudgeResult,
    TaskDefinition,
    TaskFamily,
    TaskRunResult,
    Tier,
    TrajectoryResult,
    Transcript,
)


# ---------------------------------------------------------------------------
# Test harness fixtures
# ---------------------------------------------------------------------------


def make_task(task_id: str, family: TaskFamily = TaskFamily.TOOLS) -> TaskDefinition:
    """Build a minimal valid TaskDefinition for tests."""
    from clawbench.schemas import SimulatedUser, UserTurn

    return TaskDefinition(
        id=task_id,
        name=f"Test {task_id}",
        tier=Tier.TIER1,
        family=family,
        surface="tools",
        timeout_seconds=60,
        user=SimulatedUser(turns=[UserTurn(message="test")]),
    )


def make_run_result(task_id: str, run_index: int, score: float = 0.85) -> TaskRunResult:
    return TaskRunResult(
        task_id=task_id,
        tier="tier1",
        family="tools",
        run_index=run_index,
        run_score=score,
        completion_result=CompletionResult(score=score),
        trajectory_result=TrajectoryResult(score=score),
        behavior_result=BehaviorResult(score=score),
        judge_result=JudgeResult(),
        transcript=Transcript(),
        duration_ms=1000,
        efficiency_result=EfficiencyResult(),
        delivery_outcome=DeliveryOutcome.PASS,
    )


def make_harness(concurrency: int = 1, browser_concurrency: int = 1) -> BenchmarkHarness:
    return BenchmarkHarness(
        gateway_config=GatewayConfig(),
        model="test-model",
        runs_per_task=2,
        randomize_order=False,
        quiet=True,
        print_report=False,
        concurrency=concurrency,
        browser_concurrency=browser_concurrency,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_concurrency_1_runs_serially():
    """At concurrency=1, work items must overlap by zero."""
    harness = make_harness(concurrency=1)
    tasks = [make_task("task-a"), make_task("task-b"), make_task("task-c")]

    overlap = {"max": 0, "current": 0}
    overlap_lock = asyncio.Lock()

    async def fake_run_single(task, run_index):
        async with overlap_lock:
            overlap["current"] += 1
            overlap["max"] = max(overlap["max"], overlap["current"])
        await asyncio.sleep(0.05)
        async with overlap_lock:
            overlap["current"] -= 1
        return make_run_result(task.id, run_index)

    with patch.object(harness, "_run_single", side_effect=fake_run_single):
        results = asyncio.run(harness._execute_runs(tasks))

    assert overlap["max"] == 1, f"serial run had overlap={overlap['max']}"
    assert len(results) == 3
    assert all(len(runs) == 2 for runs in results.values())
    print(f"  ✓ concurrency=1 max_overlap = {overlap['max']} (expected 1)")


def test_concurrency_4_actually_parallel():
    """At concurrency=4, multiple work items must overlap."""
    harness = make_harness(concurrency=4)
    tasks = [make_task(f"task-{i}") for i in range(4)]

    overlap = {"max": 0, "current": 0}
    overlap_lock = asyncio.Lock()

    async def fake_run_single(task, run_index):
        async with overlap_lock:
            overlap["current"] += 1
            overlap["max"] = max(overlap["max"], overlap["current"])
        await asyncio.sleep(0.1)
        async with overlap_lock:
            overlap["current"] -= 1
        return make_run_result(task.id, run_index)

    with patch.object(harness, "_run_single", side_effect=fake_run_single):
        results = asyncio.run(harness._execute_runs(tasks))

    assert overlap["max"] >= 4, f"concurrency=4 only reached overlap={overlap['max']}"
    assert len(results) == 4
    assert all(len(runs) == 2 for runs in results.values())
    print(f"  ✓ concurrency=4 max_overlap = {overlap['max']} (expected ≥4)")


def test_browser_tasks_serialized_under_high_concurrency():
    """Even at concurrency=8, browser tasks must execute one at a time."""
    harness = make_harness(concurrency=8, browser_concurrency=1)
    tasks = [
        make_task("browser-1", TaskFamily.BROWSER),
        make_task("browser-2", TaskFamily.BROWSER),
        make_task("browser-3", TaskFamily.BROWSER),
        make_task("non-browser", TaskFamily.TOOLS),
    ]

    browser_overlap = {"max": 0, "current": 0}
    browser_lock = asyncio.Lock()

    async def fake_run_single(task, run_index):
        if task.family == TaskFamily.BROWSER:
            async with browser_lock:
                browser_overlap["current"] += 1
                browser_overlap["max"] = max(browser_overlap["max"], browser_overlap["current"])
            await asyncio.sleep(0.05)
            async with browser_lock:
                browser_overlap["current"] -= 1
        else:
            await asyncio.sleep(0.01)
        return make_run_result(task.id, run_index)

    with patch.object(harness, "_run_single", side_effect=fake_run_single):
        asyncio.run(harness._execute_runs(tasks))

    assert browser_overlap["max"] == 1, (
        f"browser tasks overlapped at {browser_overlap['max']} despite browser_concurrency=1"
    )
    print(f"  ✓ browser tasks max_overlap = {browser_overlap['max']} (expected 1)")


def test_speedup_matches_theoretical_at_concurrency_4():
    """4 work items at concurrency=4 should take ~1× one work item, not 4×."""
    harness = make_harness(concurrency=4)
    tasks = [make_task(f"task-{i}") for i in range(2)]  # 2 tasks × 2 runs = 4 items
    SLEEP = 0.5

    async def fake_run_single(task, run_index):
        await asyncio.sleep(SLEEP)
        return make_run_result(task.id, run_index)

    with patch.object(harness, "_run_single", side_effect=fake_run_single):
        start = time.monotonic()
        asyncio.run(harness._execute_runs(tasks))
        elapsed = time.monotonic() - start

    # Theoretical parallel = 1 × SLEEP = 0.5s
    # Theoretical serial = 4 × SLEEP = 2.0s
    # Allow generous overhead
    assert elapsed < 1.0, f"4 items at concurrency=4 took {elapsed:.2f}s (expected ~0.5s)"
    print(f"  ✓ 4 items × 0.5s @ c=4 → wall {elapsed:.2f}s (theoretical 0.5s)")


def test_serial_takes_expected_wall_time():
    """The same workload at concurrency=1 should take linearly longer."""
    harness = make_harness(concurrency=1)
    tasks = [make_task(f"task-{i}") for i in range(2)]
    SLEEP = 0.3

    async def fake_run_single(task, run_index):
        await asyncio.sleep(SLEEP)
        return make_run_result(task.id, run_index)

    with patch.object(harness, "_run_single", side_effect=fake_run_single):
        start = time.monotonic()
        asyncio.run(harness._execute_runs(tasks))
        elapsed = time.monotonic() - start

    # Theoretical serial = 4 × 0.3 = 1.2s
    assert 1.0 < elapsed < 1.6, f"4 items serial took {elapsed:.2f}s (expected ~1.2s)"
    print(f"  ✓ 4 items × 0.3s @ c=1 → wall {elapsed:.2f}s (theoretical 1.2s)")


def test_results_preserved_in_order():
    """Concurrent execution must still return results indexed by run_index."""
    harness = make_harness(concurrency=3)
    tasks = [make_task("task-a"), make_task("task-b")]

    async def fake_run_single(task, run_index):
        # Sleep different amounts so completion order is randomized
        await asyncio.sleep(0.05 * (run_index + 1))
        return make_run_result(task.id, run_index, score=0.5 + 0.1 * run_index)

    with patch.object(harness, "_run_single", side_effect=fake_run_single):
        results = asyncio.run(harness._execute_runs(tasks))

    for task_id, runs in results.items():
        assert len(runs) == 2
        assert runs[0].run_index == 0
        assert runs[1].run_index == 1
        assert runs[0].run_score == 0.5
        assert runs[1].run_score == 0.6
    print("  ✓ results returned in correct run_index order across all tasks")


def test_browser_and_non_browser_can_overlap():
    """A non-browser task should be free to run while a browser task runs."""
    harness = make_harness(concurrency=4, browser_concurrency=1)
    tasks = [
        make_task("browser-1", TaskFamily.BROWSER),
        make_task("non-browser-1", TaskFamily.TOOLS),
        make_task("non-browser-2", TaskFamily.TOOLS),
    ]

    overall_overlap = {"max": 0, "current": 0}
    lock = asyncio.Lock()

    async def fake_run_single(task, run_index):
        async with lock:
            overall_overlap["current"] += 1
            overall_overlap["max"] = max(overall_overlap["max"], overall_overlap["current"])
        await asyncio.sleep(0.1)
        async with lock:
            overall_overlap["current"] -= 1
        return make_run_result(task.id, run_index)

    with patch.object(harness, "_run_single", side_effect=fake_run_single):
        asyncio.run(harness._execute_runs(tasks))

    assert overall_overlap["max"] >= 2, (
        f"non-browser tasks did not overlap with browser task: max={overall_overlap['max']}"
    )
    print(f"  ✓ overall max_overlap = {overall_overlap['max']} (browser + non-browser interleave)")


def main():
    tests = [
        test_concurrency_1_runs_serially,
        test_concurrency_4_actually_parallel,
        test_browser_tasks_serialized_under_high_concurrency,
        test_browser_and_non_browser_can_overlap,
        test_speedup_matches_theoretical_at_concurrency_4,
        test_serial_takes_expected_wall_time,
        test_results_preserved_in_order,
    ]
    failed = 0
    for fn in tests:
        print(f"\n=== {fn.__name__} ===")
        try:
            fn()
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            failed += 1
        except Exception:
            import traceback
            traceback.print_exc()
            failed += 1
    print()
    print("=" * 70)
    if failed:
        print(f"  {failed} of {len(tests)} parallel-harness tests FAILED")
        sys.exit(1)
    print(f"  all {len(tests)} parallel-harness tests passed")


if __name__ == "__main__":
    main()
