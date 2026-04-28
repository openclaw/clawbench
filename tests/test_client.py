from __future__ import annotations

import asyncio

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidMessage, InvalidStatus
from websockets.http11 import Response

from clawbench.client import GatewayClient, GatewayConfig, _correlate_transcript, _parse_single_message
from clawbench.schemas import Transcript


def test_gateway_config_defaults():
    cfg = GatewayConfig()
    # Defaults raised from 15s/60s -- see GatewayConfig docstring for
    # the rationale; 15s used to race gateway cold-start and produce
    # spurious empty_response failures.
    assert cfg.connect_timeout == 30.0
    assert cfg.request_timeout == 60.0


def test_gateway_config_env_overrides(monkeypatch):
    monkeypatch.setenv("CLAWBENCH_CONNECT_TIMEOUT", "45")
    monkeypatch.setenv("CLAWBENCH_REQUEST_TIMEOUT", "120")
    cfg = GatewayConfig()
    assert cfg.connect_timeout == 45.0
    assert cfg.request_timeout == 120.0


@pytest.mark.parametrize("raw", ["not-a-number", "nan", "inf", "0", "-1"])
def test_gateway_config_invalid_env_falls_back_to_default(monkeypatch, caplog, raw):
    monkeypatch.setenv("CLAWBENCH_CONNECT_TIMEOUT", raw)
    with caplog.at_level("WARNING"):
        cfg = GatewayConfig()
    assert cfg.connect_timeout == 30.0
    assert any("CLAWBENCH_CONNECT_TIMEOUT" in r.getMessage() for r in caplog.records)


def test_tool_results_are_correlated_back_to_tool_calls():
    tool_message = _parse_single_message(
        {
            "role": "assistant",
            "content": [
                {"type": "toolCall", "id": "call-1", "name": "exec", "arguments": {"command": "pytest -q"}},
            ],
        }
    )
    result_message = _parse_single_message(
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "ERROR failed test"},
            ],
        }
    )

    transcript = _correlate_transcript(Transcript(messages=[tool_message, result_message]))  # type: ignore[arg-type]
    call = transcript.tool_call_sequence[0]

    assert call.output == "ERROR failed test"
    assert call.success is False
    assert call.error == "ERROR failed test"


def test_message_usage_is_parsed_into_transcript_usage():
    message = _parse_single_message(
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done."}],
            "usage": {
                "input": 10,
                "output": 20,
                "reasoning": 5,
                "cacheRead": 3,
                "cacheWrite": 2,
                "totalTokens": 40,
                "cost": {"total": 0.0125},
            },
        }
    )

    assert message is not None
    assert message.usage.input_tokens == 10
    assert message.usage.output_tokens == 20
    assert message.usage.reasoning_tokens == 5
    assert message.usage.total_tokens == 40
    assert message.usage.total_cost_usd == 0.0125


@pytest.mark.asyncio
async def test_gateway_client_retries_transient_drain_errors(monkeypatch: pytest.MonkeyPatch):
    attempts = 0

    class FakeWebSocket:
        async def close(self) -> None:
            return None

    async def fake_connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise InvalidStatus(Response(503, "Service Unavailable", Headers()))
        return FakeWebSocket()

    async def fake_wait_event(self, event_name: str, *, timeout: float):
        return {"payload": {"nonce": ""}}

    async def fake_rpc(self, method: str, params=None):
        return {"payload": {"type": "hello-ok", "protocol": 3}}

    async def fake_listener(self):
        await asyncio.sleep(60)

    monkeypatch.setattr("clawbench.client.websockets.connect", fake_connect)
    monkeypatch.setattr(GatewayClient, "_wait_event", fake_wait_event)
    monkeypatch.setattr(GatewayClient, "_rpc", fake_rpc)
    monkeypatch.setattr(GatewayClient, "_listener", fake_listener)

    client = GatewayClient(GatewayConfig(connect_timeout=2))
    await client.connect()
    assert attempts == 2
    await client.close()


@pytest.mark.asyncio
async def test_gateway_client_retries_half_closed_handshake_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    attempts = 0

    class FakeWebSocket:
        async def close(self) -> None:
            return None

    async def fake_connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise InvalidMessage("did not receive a valid HTTP response")
        return FakeWebSocket()

    async def fake_wait_event(self, event_name: str, *, timeout: float):
        return {"payload": {"nonce": ""}}

    async def fake_rpc(self, method: str, params=None):
        return {"payload": {"type": "hello-ok", "protocol": 3}}

    async def fake_listener(self):
        await asyncio.sleep(60)

    monkeypatch.setattr("clawbench.client.websockets.connect", fake_connect)
    monkeypatch.setattr(GatewayClient, "_wait_event", fake_wait_event)
    monkeypatch.setattr(GatewayClient, "_rpc", fake_rpc)
    monkeypatch.setattr(GatewayClient, "_listener", fake_listener)

    client = GatewayClient(GatewayConfig(connect_timeout=2))
    await client.connect()
    assert attempts == 2
    await client.close()


@pytest.mark.asyncio
async def test_send_and_wait_collects_messages_that_arrive_after_final_state():
    client = GatewayClient(GatewayConfig(request_timeout=1))
    session_key = "session-1"

    async def fake_rpc(method: str, params=None):
        assert method == "sessions.send"

        async def emit() -> None:
            await asyncio.sleep(0.01)
            await client._event_queues[f"chat:{session_key}"].put({"payload": {"state": "final"}})
            await asyncio.sleep(0.2)
            await client._event_queues[f"session.message:{session_key}"].put(
                {
                    "payload": {
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Late but valid."}],
                            "usage": {"input": 1, "output": 2, "totalTokens": 3},
                        }
                    }
                }
            )

        asyncio.create_task(emit())
        return {"ok": True, "payload": {}}

    client._rpc = fake_rpc  # type: ignore[method-assign]

    transcript = await client.send_and_wait(session_key, "hello", timeout=1.0)

    assert [message.text for message in transcript.assistant_messages] == ["Late but valid."]
