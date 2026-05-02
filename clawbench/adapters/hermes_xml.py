"""Hermes agent conversation → ClawBench `Transcript` converter.

Hermes's `MiniSWERunner.run_task()` returns a dict shaped like:

```json
{
  "conversations": [
    {"from": "system", "value": "..."},
    {"from": "user", "value": "..."},
    {"from": "assistant", "value": "I'll look at the file.\\n<tool_call>{\\"name\\":\\"bash\\",\\"arguments\\":{\\"cmd\\":\\"ls\\"}}</tool_call>"},
    {"from": "tool", "value": "<tool_response>{\\"stdout\\":\\"file.py\\"}</tool_response>"},
    {"from": "assistant", "value": "<tool_call>...</tool_call>"},
    ...
  ],
  "completed": true,
  "api_calls": 7,
  "metadata": {...}
}
```

This module parses that into a canonical `Transcript` with
`TranscriptMessage` + `ToolCall` entries so the scorer / trajectory /
judge layers can score the run without any Hermes-specific knowledge.

The XML parsing is deliberately tolerant: Hermes transcripts observed
in the wild sometimes have malformed JSON inside `<tool_call>` tags
(trailing commas, unescaped newlines). We fall back to a permissive
regex extraction in that case so a single bad tool call doesn't tank
the whole transcript.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from clawbench.schemas import ToolCall, Transcript, TranscriptMessage


#: One `<tool_call>…</tool_call>` block. Non-greedy across newlines.
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(?P<body>.*?)\s*</tool_call>", re.DOTALL
)

#: One `<tool_response>…</tool_response>` block.
_TOOL_RESPONSE_RE = re.compile(
    r"<tool_response>\s*(?P<body>.*?)\s*</tool_response>", re.DOTALL
)


def _coerce_role(raw: str) -> str:
    """Normalize Hermes role labels to ClawBench `TranscriptMessage.role`.

    ClawBench uses `"user"`, `"assistant"`, `"system"`, `"tool"`. Hermes
    can emit `"human"`/`"gpt"`/`"function"` variants; we map them all
    down to the canonical vocabulary.
    """

    value = (raw or "").strip().lower()
    if value in {"assistant", "gpt", "model"}:
        return "assistant"
    if value in {"user", "human"}:
        return "user"
    if value in {"tool", "function", "tool_response"}:
        return "tool"
    if value == "system":
        return "system"
    return value or "assistant"


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Parse 0-or-more top-level JSON objects from free-form text.

    Hermes usually puts a single JSON object inside each `<tool_call>`,
    but we handle multi-object payloads defensively. Returns an empty
    list if no valid JSON is present.
    """

    text = text.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass
    # Fallback: scan for balanced `{...}` blocks. Useful when the
    # assistant wrote slightly malformed JSON. We accept a best-effort
    # parse and silently discard the rest.
    results: list[dict[str, Any]] = []
    depth = 0
    start: int | None = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        results.append(obj)
                except json.JSONDecodeError:
                    pass
                start = None
    return results


def _tool_call_from_payload(
    payload: dict[str, Any],
    *,
    index: int,
    timestamp_ms: int,
) -> ToolCall:
    """Build a canonical `ToolCall` from a Hermes `<tool_call>` payload.

    Hermes emits `{"name": "...", "arguments": {...}}` inside each
    tool_call tag. Some Nous-trained models emit slight variants —
    `"function"` for the tool name, `"parameters"` or `"input"` for
    the args. We accept any of those.
    """

    name = (
        payload.get("name")
        or payload.get("function")
        or payload.get("tool")
        or ""
    )
    arguments = (
        payload.get("arguments")
        or payload.get("parameters")
        or payload.get("args")
        or payload.get("input")
        or {}
    )
    if isinstance(arguments, str):
        # Occasionally Hermes passes a JSON-encoded string of args.
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"raw": arguments}
    if not isinstance(arguments, dict):
        arguments = {"value": arguments}
    call_id = str(payload.get("id") or payload.get("call_id") or f"hermes-{index}")
    return ToolCall(
        id=call_id,
        name=str(name),
        input=arguments,
        timestamp_ms=timestamp_ms,
    )


def _tool_response_summary(payload: dict[str, Any]) -> tuple[str, str, bool | None]:
    """Extract (output, error, success) from a `<tool_response>` payload."""

    output = ""
    error = ""
    success: bool | None = None

    stdout = payload.get("stdout")
    stderr = payload.get("stderr")
    result = payload.get("result")
    err = payload.get("error")
    msg = payload.get("message")
    status = payload.get("status")

    if isinstance(stdout, str):
        output = stdout
    elif isinstance(result, (str, dict, list)):
        output = result if isinstance(result, str) else json.dumps(result)
    elif isinstance(msg, str):
        output = msg
    if isinstance(stderr, str) and stderr.strip():
        error = stderr
    elif isinstance(err, (str, dict, list)):
        error = err if isinstance(err, str) else json.dumps(err)

    if isinstance(status, str):
        lowered = status.lower()
        if lowered in {"ok", "success", "succeeded"}:
            success = True
        elif lowered in {"error", "failed", "failure"}:
            success = False
    if error and success is None:
        success = False
    if not error and output and success is None:
        success = True
    return output, error, success


def _split_tagged(text: str, tag_re: re.Pattern[str]) -> list[tuple[str, str]]:
    """Split `text` into `(kind, body)` tuples where `kind` is `"text"` or
    `"tag"`. Preserves ordering so we can thread tool calls/responses
    back into the canonical transcript in the order they appeared.
    """

    pieces: list[tuple[str, str]] = []
    cursor = 0
    for match in tag_re.finditer(text):
        if match.start() > cursor:
            pieces.append(("text", text[cursor : match.start()]))
        pieces.append(("tag", match.group("body")))
        cursor = match.end()
    if cursor < len(text):
        pieces.append(("text", text[cursor:]))
    return pieces


def parse_conversation(result: dict[str, Any]) -> Transcript:
    """Parse a `MiniSWERunner.run_task` result dict into a `Transcript`.

    The conversation is processed in order; tool calls are emitted into
    the assistant message that contained them, and tool responses are
    paired with the most recent unpaired call. The final Transcript is
    ready for `annotate_transcript_tool_calls` → scorer.
    """

    transcript = Transcript()
    conversations = result.get("conversations") or []
    pending_calls: list[ToolCall] = []
    call_counter = 0

    for turn_index, entry in enumerate(conversations):
        if not isinstance(entry, dict):
            continue
        role = _coerce_role(str(entry.get("from", "")))
        value = str(entry.get("value", "") or "")

        # Tool responses arrive from the tool/function role.
        if role == "tool":
            for response_body in _TOOL_RESPONSE_RE.findall(value):
                payloads = _extract_json_objects(response_body)
                if not payloads:
                    payloads = [{"result": response_body}]
                for payload in payloads:
                    output, error, success = _tool_response_summary(payload)
                    if pending_calls:
                        target = pending_calls.pop(0)
                        target.output = output
                        target.error = error
                        if success is not None:
                            target.success = success
                    else:
                        # Orphan tool response — surface it as a tool
                        # message so nothing is silently dropped.
                        transcript.messages.append(
                            TranscriptMessage(
                                role="tool",
                                tool_result_content=output or error,
                            )
                        )
            continue

        # Everything else (assistant / user / system) may carry tool
        # calls plus free-form text. We interleave them faithfully.
        pieces = _split_tagged(value, _TOOL_CALL_RE)
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        for kind, body in pieces:
            if kind == "text":
                text_chunks.append(body)
            else:
                payloads = _extract_json_objects(body)
                for payload in payloads:
                    call_counter += 1
                    tool_call = _tool_call_from_payload(
                        payload,
                        index=call_counter,
                        timestamp_ms=turn_index,
                    )
                    tool_calls.append(tool_call)
                    pending_calls.append(tool_call)

        joined_text = "\n".join(chunk for chunk in text_chunks if chunk.strip()).strip()

        if role == "assistant":
            transcript.messages.append(
                TranscriptMessage(
                    role="assistant",
                    text=joined_text,
                    tool_calls=tool_calls,
                    timestamp_ms=turn_index,
                )
            )
        elif role == "user":
            transcript.messages.append(
                TranscriptMessage(
                    role="user",
                    text=joined_text,
                    timestamp_ms=turn_index,
                )
            )
        elif role == "system":
            if joined_text:
                transcript.messages.append(
                    TranscriptMessage(
                        role="system",
                        text=joined_text,
                        timestamp_ms=turn_index,
                    )
                )
        else:
            if joined_text:
                transcript.messages.append(
                    TranscriptMessage(
                        role=role,
                        text=joined_text,
                        timestamp_ms=turn_index,
                    )
                )

    return transcript


def _content_to_text(content: Any) -> str:
    """Normalize OpenAI/Anthropic-style message content to plain text."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
        return "\n".join(parts)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
    return str(content)


def _tool_call_from_chat_payload(
    payload: dict[str, Any],
    *,
    index: int,
    timestamp_ms: int,
) -> ToolCall:
    """Build a canonical tool call from chat-completions message payloads."""

    function = payload.get("function")
    if not isinstance(function, dict):
        function = {}
    name = (
        function.get("name")
        or payload.get("name")
        or payload.get("tool")
        or payload.get("type")
        or ""
    )
    arguments = (
        function.get("arguments")
        or payload.get("arguments")
        or payload.get("args")
        or payload.get("input")
        or {}
    )
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"raw": arguments}
    if not isinstance(arguments, dict):
        arguments = {"value": arguments}
    return ToolCall(
        id=str(payload.get("id") or payload.get("call_id") or f"hermes-chat-{index}"),
        name=str(name),
        input=arguments,
        timestamp_ms=timestamp_ms,
    )


def parse_chat_messages(messages: Iterable[dict[str, Any]]) -> Transcript:
    """Parse Hermes AIAgent/OpenAI-style message history to a Transcript.

    `AIAgent.run_conversation()` returns a `messages` list with user,
    assistant, and tool-role entries. This parser preserves ordering and
    attaches tool-role output back to the assistant `ToolCall` it belongs to.
    """

    transcript = Transcript()
    pending_by_id: dict[str, ToolCall] = {}
    pending_order: list[ToolCall] = []
    call_counter = 0

    for turn_index, entry in enumerate(messages):
        if not isinstance(entry, dict):
            continue
        role = _coerce_role(str(entry.get("role") or entry.get("from") or ""))
        text = _content_to_text(entry.get("content", entry.get("value", "")))

        if role == "tool":
            tool_call_id = str(entry.get("tool_call_id") or entry.get("id") or "")
            target = pending_by_id.get(tool_call_id) if tool_call_id else None
            if target is None and pending_order:
                target = pending_order.pop(0)
            if target is not None:
                target.output = text
                target.success = not _looks_like_error(text)
                if not target.success:
                    target.error = text
            elif text:
                transcript.messages.append(
                    TranscriptMessage(
                        role="tool",
                        tool_result_for=tool_call_id or None,
                        tool_result_content=text,
                        timestamp_ms=turn_index,
                    )
                )
            continue

        tool_calls: list[ToolCall] = []
        raw_calls = entry.get("tool_calls") or []
        if isinstance(raw_calls, list):
            for payload in raw_calls:
                if not isinstance(payload, dict):
                    continue
                call_counter += 1
                call = _tool_call_from_chat_payload(
                    payload,
                    index=call_counter,
                    timestamp_ms=turn_index,
                )
                tool_calls.append(call)
                pending_by_id[call.id] = call
                pending_order.append(call)

        if role == "assistant":
            transcript.messages.append(
                TranscriptMessage(
                    role="assistant",
                    text=text,
                    tool_calls=tool_calls,
                    timestamp_ms=turn_index,
                )
            )
        elif role in {"user", "system"}:
            if text:
                transcript.messages.append(
                    TranscriptMessage(
                        role=role,
                        text=text,
                        timestamp_ms=turn_index,
                    )
                )
        elif text:
            transcript.messages.append(
                TranscriptMessage(
                    role=role,
                    text=text,
                    timestamp_ms=turn_index,
                )
            )

    return transcript


def _looks_like_error(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("error", "traceback", "failed", "exception"))


def iter_tool_calls_from_conversations(conversations: Iterable[dict[str, Any]]) -> list[ToolCall]:
    """Helper used by tests: pull out just the tool-call sequence.

    Equivalent to `parse_conversation({"conversations": list(conv)}).tool_call_sequence`
    but skips the assistant-text assembly. Useful for asserting on call
    order and arguments without noise.
    """

    return parse_conversation({"conversations": list(conversations)}).tool_call_sequence


__all__ = [
    "iter_tool_calls_from_conversations",
    "parse_chat_messages",
    "parse_conversation",
]
