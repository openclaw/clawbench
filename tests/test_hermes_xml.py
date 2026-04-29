"""Tests for `clawbench.adapters.hermes_xml.parse_conversation`.

Covers the Hermes conversation shapes we expect from the wild:

- Plain assistant turn with a single tool call + a following tool_response.
- Multiple tool calls in one assistant turn.
- Assistant turn with free-form text + a tool call.
- A malformed tool_call payload — parser must recover gracefully
  (no raise; surface a best-effort call).
- Name-variant keys (`function`, `parameters`) Hermes-variant models emit.
"""

from __future__ import annotations

from clawbench.adapters.hermes_xml import (
    iter_tool_calls_from_conversations,
    parse_chat_messages,
    parse_conversation,
)
from clawbench.trajectory import annotate_transcript_tool_calls


def _conv(*entries: dict[str, str]) -> dict:
    return {"conversations": list(entries), "completed": True, "api_calls": 1}


def test_single_tool_call_with_response() -> None:
    convo = _conv(
        {"from": "system", "value": "You are a helpful coding agent."},
        {"from": "user", "value": "List files."},
        {
            "from": "assistant",
            "value": "I'll run `ls`.\n"
                     '<tool_call>{"name":"bash","arguments":{"cmd":"ls"}}</tool_call>',
        },
        {
            "from": "tool",
            "value": '<tool_response>{"stdout":"main.py\\nREADME"}</tool_response>',
        },
    )
    transcript = parse_conversation(convo)
    calls = transcript.tool_call_sequence
    assert len(calls) == 1
    assert calls[0].name == "bash"
    assert calls[0].input == {"cmd": "ls"}
    assert "main.py" in calls[0].output
    assert calls[0].success is True

    # Assistant text preserved, tool-call body stripped out.
    assistant = next(
        msg for msg in transcript.messages if msg.role == "assistant"
    )
    assert "I'll run `ls`." in assistant.text
    assert "<tool_call>" not in assistant.text


def test_multiple_tool_calls_in_one_turn() -> None:
    convo = _conv(
        {
            "from": "assistant",
            "value": (
                '<tool_call>{"name":"bash","arguments":{"cmd":"ls"}}</tool_call>'
                '<tool_call>{"name":"bash","arguments":{"cmd":"pwd"}}</tool_call>'
            ),
        },
        {
            "from": "tool",
            "value": '<tool_response>{"stdout":"a"}</tool_response>',
        },
        {
            "from": "tool",
            "value": '<tool_response>{"stdout":"/tmp"}</tool_response>',
        },
    )
    calls = iter_tool_calls_from_conversations(convo["conversations"])
    assert len(calls) == 2
    assert calls[0].input == {"cmd": "ls"}
    assert calls[1].input == {"cmd": "pwd"}
    assert calls[0].output == "a"
    assert calls[1].output == "/tmp"


def test_malformed_json_falls_back_to_best_effort() -> None:
    convo = _conv(
        {
            "from": "assistant",
            "value": (
                '<tool_call>{"name":"bash","arguments":{"cmd":"ls"} <-- stray text }</tool_call>'
                '<tool_call>{"name":"bash","arguments":{"cmd":"pwd"}}</tool_call>'
            ),
        },
    )
    calls = iter_tool_calls_from_conversations(convo["conversations"])
    # First is malformed; parser recovers one or two calls without
    # raising, and the clean second call is always captured.
    assert len(calls) >= 1
    assert any(c.input == {"cmd": "pwd"} for c in calls)


def test_name_variants_are_accepted() -> None:
    convo = _conv(
        {
            "from": "assistant",
            "value": (
                '<tool_call>{"function":"bash","parameters":{"cmd":"ls"}}</tool_call>'
            ),
        },
    )
    calls = iter_tool_calls_from_conversations(convo["conversations"])
    assert len(calls) == 1
    assert calls[0].name == "bash"
    assert calls[0].input == {"cmd": "ls"}


def test_tool_error_marks_call_failed() -> None:
    convo = _conv(
        {
            "from": "assistant",
            "value": '<tool_call>{"name":"bash","arguments":{"cmd":"nonsense"}}</tool_call>',
        },
        {
            "from": "tool",
            "value": '<tool_response>{"stderr":"command not found","status":"error"}</tool_response>',
        },
    )
    calls = iter_tool_calls_from_conversations(convo["conversations"])
    assert len(calls) == 1
    assert calls[0].success is False
    assert "command not found" in calls[0].error


def test_orphan_tool_response_not_silently_dropped() -> None:
    convo = _conv(
        {
            "from": "tool",
            "value": '<tool_response>{"stdout":"nothing to pair with"}</tool_response>',
        },
    )
    transcript = parse_conversation(convo)
    # No calls, but one tool-role transcript message surfaces the output.
    assert transcript.tool_call_sequence == []
    tool_messages = [msg for msg in transcript.messages if msg.role == "tool"]
    assert tool_messages
    assert "nothing to pair" in tool_messages[0].tool_result_content


def test_parser_output_annotates_with_canonical_families() -> None:
    convo = _conv(
        {
            "from": "assistant",
            "value": (
                '<tool_call>{"name":"str_replace_based_edit_tool",'
                '"arguments":{"path":"main.py","old":"a","new":"b"}}</tool_call>'
            ),
        },
    )
    transcript = parse_conversation(convo)
    # Running the existing trajectory classifier over the parsed
    # transcript should assign a canonical family tag to every call.
    annotated = annotate_transcript_tool_calls(transcript)
    families = [c.family for c in annotated.tool_call_sequence]
    assert all(f for f in families), f"expected every call to get a family tag, got {families}"
    assert families == ["edit"]


def test_parse_chat_messages_pairs_tool_results() -> None:
    transcript = parse_chat_messages(
        [
            {"role": "user", "content": "List files"},
            {
                "role": "assistant",
                "content": "I'll inspect.",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "terminal",
                            "arguments": "{\"command\":\"ls\"}",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "main.py"},
            {"role": "assistant", "content": "Found main.py"},
        ]
    )

    calls = transcript.tool_call_sequence
    assert len(calls) == 1
    assert calls[0].name == "terminal"
    assert calls[0].input == {"command": "ls"}
    assert calls[0].output == "main.py"
    assert transcript.assistant_messages[-1].text == "Found main.py"
