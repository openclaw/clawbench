"""Completion verification — OpenClaw-aware entry point.

Historically this module contained both agent-agnostic verification
primitives (file states, execution checks, workspace memory scans, JSON
path resolution) and OpenClaw-specific verifiers that reach into the
gateway via RPCs (`memory.search`, `sessions.resolve`, `cron.list`,
arbitrary `_rpc(method)`).

Phase-4 splits them:

- The agent-agnostic primitives now live in `clawbench.environment_files`
  and are used by every adapter.
- The OpenClaw-specific primitives stay here for now and will move into
  `clawbench/adapters/openclaw.py` once the adapter wiring lands in a
  later step.

The public surface — `verify_completion`, `run_execution_check`, module-
level helpers — stays unchanged so existing callers (harness, scorer,
tests) keep working. Function bodies that used to do real work now
delegate to `environment_files` to keep behavior identical.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from clawbench.client import GatewayClient
from clawbench.environment_files import (
    MEMORY_FILE_CANDIDATES,
    evaluate_execution_result as _evaluate_execution_result_impl,
    memory_visible_in_transcript as _memory_visible_in_transcript_impl,
    read_workspace_memory_text,
    resolve_json_path,
    run_execution_check as _run_execution_check_impl,
    verify_file_state as _verify_file_state_impl,
    verify_memory_fallback,
)
from clawbench.schemas import (
    CompletionResult,
    CompletionSpec,
    CronState,
    ExecutionCheck,
    ExecutionCheckResult,
    FileState,
    GatewayAssertion,
    MemoryState,
    SessionState,
    Transcript,
)

logger = logging.getLogger(__name__)


async def verify_completion(
    completion: CompletionSpec,
    *,
    workspace: Path,
    client: GatewayClient,
    session_key: str,
    agent_id: str | None = None,
    runtime_values: dict[str, Any],
    transcript: Transcript | None = None,
) -> CompletionResult:
    total = 0
    passed = 0
    failures: list[str] = []
    execution_results: list[ExecutionCheckResult] = []

    for spec in completion.files:
        ok, reason = _verify_file(spec, workspace, runtime_values)
        total += 1
        if ok:
            passed += 1
        else:
            failures.append(f"FILE {spec.path}: {reason}")

    for spec in completion.memory:
        ok, reason = await _verify_memory(
            spec, client, session_key, agent_id=agent_id, transcript=transcript, workspace=workspace
        )
        total += 1
        if ok:
            passed += 1
        else:
            failures.append(f"MEMORY {spec.key_pattern}: {reason}")

    if completion.session:
        ok, reason = await _verify_session(completion.session, client, session_key)
        total += 1
        if ok:
            passed += 1
        else:
            failures.append(f"SESSION: {reason}")

    for spec in completion.cron:
        ok, reason = await _verify_cron(spec, client)
        total += 1
        if ok:
            passed += 1
        else:
            failures.append(f"CRON: {reason}")

    for spec in completion.gateway_assertions:
        ok, reason = await _verify_gateway_assertion(spec, client)
        total += 1
        if ok:
            passed += 1
        else:
            failures.append(f"GATEWAY {spec.method}:{spec.assert_path}: {reason}")

    for spec in completion.execution_checks:
        result = await run_execution_check(spec, workspace=workspace, runtime_values=runtime_values)
        execution_results.append(result)
        total += 1
        if result.passed:
            passed += 1
        else:
            failures.append(f"EXEC {spec.name}: {result.reason}")

    score = passed / total if total else 1.0
    return CompletionResult(
        total_assertions=total,
        passed_assertions=passed,
        failed_assertions=failures,
        execution_results=execution_results,
        score=round(score, 4),
    )


# ---------------------------------------------------------------------------
# Agent-agnostic primitives — re-exported via delegates so historical
# callers that import from `clawbench.environment` keep working.
# ---------------------------------------------------------------------------


async def run_execution_check(
    spec: ExecutionCheck,
    *,
    workspace: Path,
    runtime_values: dict[str, Any],
) -> ExecutionCheckResult:
    return await _run_execution_check_impl(
        spec, workspace=workspace, runtime_values=runtime_values
    )


def _evaluate_execution_result(
    spec: ExecutionCheck,
    workspace: Path,
    runtime_values: dict[str, Any],
    exit_code: int,
    stdout: str,
    stderr: str,
) -> tuple[bool, str]:
    return _evaluate_execution_result_impl(
        spec, workspace, runtime_values, exit_code, stdout, stderr
    )


def _verify_file(spec: FileState, workspace: Path, runtime_values: dict[str, Any]) -> tuple[bool, str]:
    return _verify_file_state_impl(spec, workspace, runtime_values)


def _memory_visible_in_transcript(spec: MemoryState, transcript: Transcript) -> bool:
    return _memory_visible_in_transcript_impl(spec, transcript)


def _resolve_path(payload: Any, path: str) -> Any:
    return resolve_json_path(payload, path)


# ---------------------------------------------------------------------------
# OpenClaw-tied verifiers. These call `GatewayClient` RPCs; they will
# migrate into `adapters/openclaw.py` once the adapter wiring lands.
# ---------------------------------------------------------------------------


async def _verify_memory(
    spec: MemoryState,
    client: GatewayClient,
    session_key: str,
    *,
    agent_id: str | None = None,
    transcript: Transcript | None = None,
    workspace: Path | None = None,
) -> tuple[bool, str]:
    try:
        response = await client._rpc(
            "memory.search",
            {
                "query": spec.key_pattern,
                "sessionKey": session_key,
                "limit": 20,
            },
        )
        entries = response.get("payload", {}).get("entries", [])
        if not spec.exists:
            return (not entries, "Correctly absent" if not entries else "Memory entry exists")
        if not entries:
            return False, "No matching memory entries found"
        all_values = " ".join(str(entry.get("value", "")) for entry in entries)
        for token in spec.value_contains:
            if token.lower() not in all_values.lower():
                return False, f"Memory value missing '{token}'"
        return True, "OK"
    except Exception as exc:
        logger.info(
            "memory.search unavailable for verification, falling back to agent memory files: %s",
            exc,
        )

    # Fallback path: pull the same set of memory files the agent would
    # produce (MEMORY.md, memory/notes.md, …) via the gateway, then hand
    # the resulting text to the shared filesystem-fallback resolver in
    # `environment_files`. If no gateway is available (agent_id is None
    # or the calls error) and a workspace was supplied, fall back further
    # to scanning the workspace filesystem directly.

    extra_memory_text = ""
    if agent_id:
        try:
            extra_memory_text = await _read_agent_memory_text(client, agent_id)
        except Exception:
            extra_memory_text = ""

    if workspace is not None:
        return verify_memory_fallback(
            spec,
            workspace,
            transcript=transcript,
            extra_memory_text=extra_memory_text,
        )

    if not agent_id:
        return False, "memory.search unavailable and no agent id was provided for fallback verification"

    # Legacy pre-workspace path: agent_id is set but we don't have a
    # workspace handle. Resolve using only the gateway-sourced text +
    # transcript scan to preserve the exact prior behavior.
    normalized = extra_memory_text.lower()
    needle = spec.key_pattern.lower()
    found = needle in normalized
    if not spec.exists:
        return (not found, "Correctly absent" if not found else "Memory entry exists")
    if found:
        for token in spec.value_contains:
            if token.lower() not in normalized:
                return False, f"Memory value missing '{token}'"
        return True, "OK"
    if transcript and _memory_visible_in_transcript(spec, transcript):
        return True, "Verified from transcript fallback"
    return (
        False,
        "No matching memory content found in persisted memory files or transcript fallback",
    )


async def _read_agent_memory_text(client: GatewayClient, agent_id: str) -> str:
    contents: list[str] = []
    for file_name in MEMORY_FILE_CANDIDATES:
        try:
            payload = await client.get_agent_file(agent_id, file_name)
        except Exception:
            continue
        file_entry = payload.get("file", {})
        content = file_entry.get("content", "")
        if isinstance(content, str) and content.strip():
            contents.append(content)
    return "\n".join(contents)


async def _verify_session(
    spec: SessionState,
    client: GatewayClient,
    session_key: str,
) -> tuple[bool, str]:
    try:
        response = await client._rpc("sessions.resolve", {"key": session_key})
        payload = response.get("payload", {})
        if not spec.should_exist:
            return False, "Session exists but should not"
        if spec.model_should_be:
            actual = str(payload.get("model", ""))
            if spec.model_should_be.lower() not in actual.lower():
                return False, f"Model mismatch: expected {spec.model_should_be}, got {actual}"
        return True, "OK"
    except Exception as exc:
        if not spec.should_exist:
            return True, "Correctly absent"
        return False, str(exc)


async def _verify_cron(spec: CronState, client: GatewayClient) -> tuple[bool, str]:
    try:
        response = await client._rpc("cron.list", {})
        jobs = response.get("payload", {}).get("jobs", [])
        if not spec.exists:
            return (not jobs, "Correctly absent" if not jobs else "Cron jobs exist")
        if not jobs:
            return False, "No cron jobs found"
        if spec.description_contains and not any(
            spec.description_contains.lower() in json.dumps(job).lower() for job in jobs
        ):
            return False, f"No cron job matched '{spec.description_contains}'"
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


async def _verify_gateway_assertion(
    spec: GatewayAssertion,
    client: GatewayClient,
) -> tuple[bool, str]:
    try:
        response = await client._rpc(spec.method, spec.params)
        payload = response.get("payload", {})
        value = resolve_json_path(payload, spec.assert_path)
        if not spec.assert_exists:
            return (value is None, "Correctly absent" if value is None else "Path exists")
        if value is None:
            return False, f"Path {spec.assert_path} not found"
        if spec.assert_equals is not None and value != spec.assert_equals:
            return False, f"Expected {spec.assert_equals}, got {value}"
        if spec.assert_contains is not None and spec.assert_contains.lower() not in str(value).lower():
            return False, f"Expected '{spec.assert_contains}' in {value}"
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


# Backward-compatible names for any external users that imported the
# private delegates directly. The old symbols resolve to the new ones.
_verify_file_state = _verify_file
_verify_execution = _evaluate_execution_result_impl


__all__ = [
    "run_execution_check",
    "verify_completion",
]
