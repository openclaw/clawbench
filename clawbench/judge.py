"""Optional LLM-as-judge sidecar for nuanced task quality checks."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from clawbench.client import GatewayClient
from clawbench.paths import resolve_workspace_path
from clawbench.session_labels import unique_session_label
from clawbench.schemas import (
    CompletionResult,
    JudgeExpectations,
    JudgeResult,
    TaskDefinition,
    Transcript,
)

logger = logging.getLogger(__name__)


async def judge_task_run(
    *,
    task: TaskDefinition,
    transcript: Transcript,
    workspace: Path,
    client: GatewayClient,
    judge_model: str,
    completion_result: CompletionResult,
) -> JudgeResult:
    if not judge_model or task.judge is None:
        return JudgeResult()

    prompt = build_judge_prompt(
        task=task,
        judge=task.judge,
        transcript=transcript,
        workspace=workspace,
        completion_result=completion_result,
    )
    session_key = ""
    started_at = time.monotonic()
    try:
        session_key = await client.create_session(
            model=judge_model,
            label=unique_session_label(f"clawbench-judge-{task.id}"),
        )
        await client.subscribe(session_key)
        judge_transcript = await client.send_and_wait(session_key, prompt)
        raw_text = judge_transcript.assistant_text
        parsed = parse_judge_response(
            raw_text,
            passing_threshold=task.judge.passing_threshold,
        )
        if parsed.error:
            logger.warning(
                "Judge parse failed for %s: %s (response length=%d)",
                task.id,
                parsed.error,
                len(raw_text or ""),
            )
        parsed.enabled = True
        parsed.model = judge_model
        parsed.duration_ms = int((time.monotonic() - started_at) * 1000)
        parsed.token_usage = judge_transcript.total_usage
        return parsed
    except Exception as exc:
        logger.warning("LLM judge failed for %s: %s", task.id, exc)
        return JudgeResult(
            enabled=True,
            model=judge_model,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error=str(exc),
            reason="Judge execution failed.",
        )
    finally:
        if session_key:
            try:
                await client.delete_session(session_key)
            except Exception as exc:
                logger.warning("Failed to delete judge session %s: %s", session_key, exc)


def build_judge_prompt(
    *,
    task: TaskDefinition,
    judge: JudgeExpectations,
    transcript: Transcript,
    workspace: Path,
    completion_result: CompletionResult,
) -> str:
    sections = [
        "You are evaluating one ClawBench agent run.",
        "Score only the task-specific quality rubric below.",
        (
            "Return JSON only with keys "
            '"score", "confidence", "reason", "rubric_hits", and "rubric_misses".'
        ),
        "Do not use tools. Do not add markdown.",
        "",
        f"Task ID: {task.id}",
        f"Task name: {task.name}",
        f"Judge threshold: {judge.passing_threshold:.2f}",
        "Rubric:",
        judge.rubric.strip(),
    ]

    if judge.include_completion_feedback:
        sections.extend(
            [
                "",
                "Deterministic verifier summary:",
                (
                    f"- completion assertions: {completion_result.passed_assertions}/"
                    f"{completion_result.total_assertions}"
                ),
                f"- completion score: {completion_result.score:.3f}",
            ]
        )
        if completion_result.failed_assertions:
            sections.append("- failures:")
            sections.extend(f"  - {failure}" for failure in completion_result.failed_assertions[:6])

    artifact_block = _render_artifacts(
        artifact_paths=judge.artifact_paths,
        workspace=workspace,
        max_chars=judge.max_artifact_chars,
    )
    if artifact_block:
        sections.extend(["", "Artifacts:", artifact_block])

    if judge.include_transcript:
        transcript_block = _render_transcript_excerpt(
            transcript=transcript,
            max_chars=judge.max_transcript_chars,
        )
        if transcript_block:
            sections.extend(["", "Transcript excerpt:", transcript_block])

    sections.extend(
        [
            "",
            "Scoring guidance:",
            "- 1.0 means the output is fully correct, grounded, and high quality for this rubric.",
            "- 0.7 means acceptable and usable.",
            "- 0.4 means partial or shaky.",
            "- 0.0 means missing, wrong, unsafe, or hallucinated.",
        ]
    )
    return "\n".join(sections).strip()


def parse_judge_response(raw_text: str, *, passing_threshold: float) -> JudgeResult:
    payload = _extract_json_payload(raw_text)
    if payload is None:
        payload = _extract_labeled_payload(raw_text)
    if payload is None:
        return JudgeResult(
            enabled=True,
            error="Judge response did not contain valid JSON.",
            reason=_truncate_text(raw_text.strip(), 600),
        )

    score = _clamp_float(payload.get("score", 0.0))
    confidence = _clamp_float(payload.get("confidence", 0.0))
    return JudgeResult(
        enabled=True,
        score=score,
        confidence=confidence,
        passed=score >= passing_threshold,
        reason=_truncate_text(str(payload.get("reason", "")).strip(), 600),
        rubric_hits=_coerce_string_list(payload.get("rubric_hits")),
        rubric_misses=_coerce_string_list(payload.get("rubric_misses")),
    )


def _render_artifacts(*, artifact_paths: list[str], workspace: Path, max_chars: int) -> str:
    if not artifact_paths or max_chars <= 0:
        return ""

    remaining = max_chars
    blocks: list[str] = []
    for rel_path in artifact_paths:
        try:
            target = resolve_workspace_path(
                workspace,
                rel_path,
                field=f"judge artifact {rel_path}",
            )
        except ValueError as exc:
            block = f"=== {rel_path} ===\n(invalid path: {exc})"
        else:
            if not target.exists():
                block = f"=== {rel_path} ===\n(missing)"
            elif target.is_dir():
                block = f"=== {rel_path} ===\n(directory)"
            else:
                content = target.read_text(encoding="utf-8", errors="replace")
                block = f"=== {rel_path} ===\n{_truncate_text(content, max(0, remaining - len(rel_path) - 20))}"

        if remaining <= 0:
            break
        if len(block) > remaining:
            block = _truncate_text(block, remaining)
        blocks.append(block)
        remaining -= len(block) + 2

    return "\n\n".join(blocks)


def _render_transcript_excerpt(*, transcript: Transcript, max_chars: int) -> str:
    if max_chars <= 0:
        return ""

    family_counts = Counter(call.family or call.name for call in transcript.tool_call_sequence)
    failed_calls = [
        f"{call.family or call.name}: {call.error or call.output}"
        for call in transcript.tool_call_sequence
        if call.success is False and (call.error or call.output)
    ]
    header_lines = []
    if family_counts:
        header_lines.append(
            "tool families: "
            + ", ".join(f"{family} x{count}" for family, count in sorted(family_counts.items()))
        )
    if failed_calls:
        header_lines.append("tool failures:")
        header_lines.extend(f"  - {_truncate_text(item, 180)}" for item in failed_calls[:5])

    message_lines: list[str] = []
    for message in transcript.messages[-10:]:
        text = message.text.strip()
        if not text and not message.tool_calls:
            continue
        role_label = message.role.upper()
        if text:
            message_lines.append(f"[{role_label}] {_truncate_text(text, 500)}")
        for call in message.tool_calls[:4]:
            tool_state = "ok" if call.success is not False else "failed"
            message_lines.append(f"[{role_label} TOOL] {call.family or call.name} ({tool_state})")

    combined = "\n".join([*header_lines, *message_lines]).strip()
    return _truncate_text(combined, max_chars)


def _extract_json_payload(raw_text: str) -> dict[str, Any] | None:
    candidate = raw_text.strip()
    if not candidate:
        return None

    for attempt in (candidate, _strip_code_fences(candidate), _slice_json_candidate(candidate)):
        if not attempt:
            continue
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _slice_json_candidate(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + 1]


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _extract_labeled_payload(raw_text: str) -> dict[str, Any] | None:
    score = _extract_number(raw_text, "score")
    confidence = _extract_number(raw_text, "confidence")
    if score is None and confidence is None:
        return None
    return {
        "score": 0.0 if score is None else score,
        "confidence": 0.0 if confidence is None else confidence,
        "reason": _extract_reason(raw_text),
        "rubric_hits": _extract_labeled_list(raw_text, "rubric_hits", "hits"),
        "rubric_misses": _extract_labeled_list(raw_text, "rubric_misses", "misses"),
    }


def _extract_number(text: str, label: str) -> float | None:
    match = re.search(rf'(?im)^\s*"?{re.escape(label)}"?\s*[:=]\s*([0-9]*\.?[0-9]+)', text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_reason(text: str) -> str:
    match = re.search(
        r'(?ims)^\s*"?reason"?\s*[:=]\s*(.+?)(?=^\s*"?(?:rubric[_ ]?hits|hits|rubric[_ ]?misses|misses|score|confidence)"?\s*[:=]|\Z)',
        text,
    )
    if not match:
        return ""
    return _truncate_text(match.group(1).strip().strip('"').strip("'"), 600)


def _extract_labeled_list(text: str, *labels: str) -> list[str]:
    label_pattern = "|".join(re.escape(label) for label in labels)
    inline_match = re.search(rf'(?im)^\s*"?(?:{label_pattern})"?\s*[:=][ \t]*(.+)$', text)
    if inline_match:
        raw = inline_match.group(1).strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return _coerce_string_list(parsed)
        if raw and raw.lower() not in {"none", "[]"}:
            return [item.strip() for item in re.split(r"\s*,\s*", raw) if item.strip()]

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(rf'^\s*"?(?:{label_pattern})"?\s*:?\s*$', line, re.IGNORECASE):
            continue
        items: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            stripped = lines[cursor].strip()
            if not stripped:
                cursor += 1
                continue
            if not re.match(r"^[-*]\s+", stripped):
                break
            items.append(re.sub(r"^[-*]\s+", "", stripped).strip())
            cursor += 1
        return items
    return []


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_truncate_text(str(item).strip(), 200) for item in value if str(item).strip()]


def _clamp_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[: limit - 3]}..."
