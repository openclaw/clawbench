"""Gateway client using WebSocket protocol v3."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import InvalidMessage, InvalidStatus

from clawbench import __version__
from clawbench.schemas import TokenUsage, ToolCall, Transcript, TranscriptMessage

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 3
DEVICE_IDENTITY_HELPER_JS = r"""
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function base64UrlEncode(buf) {
  return buf.toString("base64").replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/g, "");
}

function derivePublicKeyRaw(publicKeyPem) {
  const key = crypto.createPublicKey(publicKeyPem);
  const spki = key.export({ type: "spki", format: "der" });
  if (
    spki.length === ED25519_SPKI_PREFIX.length + 32 &&
    spki.subarray(0, ED25519_SPKI_PREFIX.length).equals(ED25519_SPKI_PREFIX)
  ) {
    return spki.subarray(ED25519_SPKI_PREFIX.length);
  }
  return spki;
}

function fingerprintPublicKey(publicKeyPem) {
  return crypto.createHash("sha256").update(derivePublicKeyRaw(publicKeyPem)).digest("hex");
}

function generateIdentity() {
    const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519", {});
  const publicKeyPem = publicKey.export({ type: "spki", format: "pem" }).toString();
  const privateKeyPem = privateKey.export({ type: "pkcs8", format: "pem" }).toString();
  return {
    deviceId: fingerprintPublicKey(publicKeyPem),
    publicKeyPem,
    privateKeyPem,
  };
}

function loadOrCreateDeviceIdentity(filePath) {
  try {
    if (fs.existsSync(filePath)) {
      const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
      if (
        parsed &&
        parsed.version === 1 &&
        typeof parsed.deviceId === "string" &&
        typeof parsed.publicKeyPem === "string" &&
        typeof parsed.privateKeyPem === "string"
      ) {
        const derivedId = fingerprintPublicKey(parsed.publicKeyPem);
        if (derivedId !== parsed.deviceId) {
          parsed.deviceId = derivedId;
          fs.mkdirSync(path.dirname(filePath), { recursive: true });
          fs.writeFileSync(filePath, `${JSON.stringify(parsed, null, 2)}\n`, { mode: 0o600 });
        }
        return {
          deviceId: derivedId,
          publicKeyPem: parsed.publicKeyPem,
          privateKeyPem: parsed.privateKeyPem,
        };
      }
    }
  } catch {
    // fall through to regenerate
  }

  const identity = generateIdentity();
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(
    filePath,
    `${JSON.stringify(
      {
        version: 1,
        deviceId: identity.deviceId,
        publicKeyPem: identity.publicKeyPem,
        privateKeyPem: identity.privateKeyPem,
        createdAtMs: Date.now(),
      },
      null,
      2,
    )}\n`,
    { mode: 0o600 },
  );
  return identity;
}

function normalizeMetadata(value) {
  if (typeof value !== "string") {
    return "";
  }
  const trimmed = value.trim();
  return trimmed ? trimmed.replace(/[A-Z]/g, (char) => String.fromCharCode(char.charCodeAt(0) + 32)) : "";
}

const params = JSON.parse(fs.readFileSync(0, "utf8"));
const stateDir = process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), ".openclaw");
const identityPath = path.join(stateDir, "identity", "device.json");
const identity = loadOrCreateDeviceIdentity(identityPath);
const signedAtMs = Date.now();
const payload = [
  "v3",
  identity.deviceId,
  params.clientId,
  params.clientMode,
  params.role,
  Array.isArray(params.scopes) ? params.scopes.join(",") : "",
  String(signedAtMs),
  typeof params.token === "string" ? params.token : "",
  params.nonce,
  normalizeMetadata(params.platform),
  normalizeMetadata(params.deviceFamily),
].join("|");
const signature = base64UrlEncode(
  crypto.sign(null, Buffer.from(payload, "utf8"), crypto.createPrivateKey(identity.privateKeyPem)),
);

process.stdout.write(
  JSON.stringify({
    id: identity.deviceId,
    publicKey: base64UrlEncode(derivePublicKeyRaw(identity.publicKeyPem)),
    signature,
    signedAt: signedAtMs,
    nonce: params.nonce,
  }),
);
"""


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back on the default.

    Bad, non-finite, or non-positive values are ignored (we log a
    warning below) so a typo in ``CLAWBENCH_CONNECT_TIMEOUT`` never
    silently bricks a run.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("ignoring invalid %s=%r; using default %s", name, raw, default)
        return default
    if not math.isfinite(value) or value <= 0:
        logger.warning("ignoring invalid %s=%r; using default %s", name, raw, default)
        return default
    return value


@dataclass
class GatewayConfig:
    url: str = "ws://localhost:18789"
    token: str = ""
    # First WebSocket connect + protocol handshake + auth challenge.
    # In practice the gateway is up on ``/healthz`` well before the WS
    # handshake path is fully ready in containerised / cold-start
    # setups -- we've measured ``phase0_session_setup`` ~ 20-25s against
    # a newly started OpenClaw gateway -- so 15s is too aggressive and
    # turns into spurious ``empty_response`` failures at the task level.
    # 30s is still short enough to fail fast on a truly wedged gateway.
    # Override with env var ``CLAWBENCH_CONNECT_TIMEOUT``.
    connect_timeout: float = field(
        default_factory=lambda: _env_float("CLAWBENCH_CONNECT_TIMEOUT", 30.0)
    )
    # Per-RPC timeout. 60s is generous for what are sub-second calls
    # in steady state (agents.create, sessions.create, etc.); fail-fast
    # here converts a transient gateway hang into a recoverable error
    # instead of a 5-minute worker stall. Long-running operations like
    # send_and_wait pass an explicit per-call timeout and do not use
    # this default.  Override with env var ``CLAWBENCH_REQUEST_TIMEOUT``.
    request_timeout: float = field(
        default_factory=lambda: _env_float("CLAWBENCH_REQUEST_TIMEOUT", 60.0)
    )


class GatewayClient:
    """Persistent WebSocket client for the OpenClaw gateway."""

    def __init__(self, config: GatewayConfig | None = None) -> None:
        self.config = config or GatewayConfig()
        self._ws: ClientConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._listen_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> GatewayClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        host = self.config.url.replace("ws://", "http://").replace("wss://", "https://")
        deadline = asyncio.get_running_loop().time() + self.config.connect_timeout
        attempt = 0
        while True:
            attempt += 1
            try:
                remaining = max(1.0, deadline - asyncio.get_running_loop().time())
                self._ws = await websockets.connect(
                    self.config.url,
                    max_size=10 * 1024 * 1024,
                    open_timeout=min(self.config.connect_timeout, remaining),
                    additional_headers={"Origin": host},
                )
                break
            except Exception as exc:
                if not _is_transient_gateway_connect_error(exc):
                    raise
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                delay = min(1.5, 0.25 * attempt)
                logger.info(
                    "Gateway connect transient failure (%s); retrying in %.2fs",
                    _describe_connect_error(exc),
                    delay,
                )
                await asyncio.sleep(delay)
        self._listen_task = asyncio.create_task(self._listener())
        challenge = await self._wait_event("connect.challenge", timeout=self.config.connect_timeout)
        challenge_payload = challenge.get("payload", {})
        nonce = ""
        if isinstance(challenge_payload, dict):
            raw_nonce = challenge_payload.get("nonce", "")
            if isinstance(raw_nonce, str):
                nonce = raw_nonce.strip()

        role = "operator"
        scopes = [
            "operator.admin",
            "operator.read",
            "operator.write",
            "operator.approvals",
            "operator.pairing",
        ]
        client_info = {
            "id": "openclaw-control-ui",
            "version": __version__,
            "platform": "linux",
            "mode": "ui",
        }
        connect_params: dict[str, Any] = {
            "minProtocol": PROTOCOL_VERSION,
            "maxProtocol": PROTOCOL_VERSION,
            "client": client_info,
            "role": role,
            "scopes": scopes,
            "caps": [],
            "commands": [],
            "permissions": {},
            "auth": {"token": self.config.token} if self.config.token else {},
        }
        device = _build_connect_device(
            nonce=nonce,
            token=self.config.token,
            client_id=str(client_info["id"]),
            client_mode=str(client_info["mode"]),
            role=role,
            scopes=scopes,
            platform=str(client_info["platform"]),
        )
        if device:
            connect_params["device"] = device

        response = await self._rpc(
            "connect",
            connect_params,
        )
        payload = response.get("payload", {})
        if payload.get("type") != "hello-ok":
            raise ConnectionError(f"Expected hello-ok, got: {payload}")
        logger.info("Connected to gateway (protocol v%s)", payload.get("protocol", "?"))

    async def close(self) -> None:
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def create_session(
        self,
        *,
        model: str | None = None,
        agent_id: str | None = None,
        label: str | None = None,
    ) -> str:
        params: dict[str, Any] = {}
        if model:
            params["model"] = model
        if agent_id:
            params["agentId"] = agent_id
        if label:
            params["label"] = label
        response = await self._rpc("sessions.create", params)
        payload = response.get("payload", {})
        key = payload.get("sessionKey") or payload.get("key", "")
        if not key:
            raise RuntimeError(f"sessions.create returned no key: {payload}")
        return key

    async def create_agent(
        self,
        *,
        name: str,
        workspace: str,
        emoji: str | None = None,
        avatar: str | None = None,
    ) -> str:
        params: dict[str, Any] = {
            "name": name,
            "workspace": workspace,
        }
        if emoji:
            params["emoji"] = emoji
        if avatar:
            params["avatar"] = avatar
        response = await self._rpc("agents.create", params)
        payload = response.get("payload", {})
        agent_id = payload.get("agentId", "")
        if not agent_id:
            raise RuntimeError(f"agents.create returned no agentId: {payload}")
        return str(agent_id)

    async def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        workspace: str | None = None,
        model: str | None = None,
        avatar: str | None = None,
    ) -> None:
        params: dict[str, Any] = {"agentId": agent_id}
        if name:
            params["name"] = name
        if workspace:
            params["workspace"] = workspace
        if model:
            params["model"] = model
        if avatar is not None:
            params["avatar"] = avatar
        await self._rpc("agents.update", params)

    async def delete_agent(self, agent_id: str, *, delete_files: bool = False) -> None:
        try:
            await self._rpc("agents.delete", {"agentId": agent_id, "deleteFiles": delete_files})
        except Exception as exc:
            logger.warning("Failed to delete agent %s: %s", agent_id, exc)

    async def get_agent_file(self, agent_id: str, name: str) -> dict[str, Any]:
        response = await self._rpc("agents.files.get", {"agentId": agent_id, "name": name})
        return response.get("payload", {})

    async def subscribe(self, session_key: str) -> None:
        await self._rpc("sessions.messages.subscribe", {"key": session_key})

    async def delete_session(self, session_key: str) -> None:
        try:
            await self._rpc("sessions.delete", {"key": session_key})
        except Exception as exc:
            logger.warning("Failed to delete session %s: %s", session_key, exc)

    async def get_effective_tools(self, session_key: str) -> dict[str, Any]:
        response = await self._rpc("tools.effective", {"sessionKey": session_key})
        return response.get("payload", {})

    async def send_and_wait(
        self,
        session_key: str,
        message: str,
        *,
        timeout: float | None = None,
    ) -> Transcript:
        timeout = timeout or self.config.request_timeout
        idempotency_key = str(uuid.uuid4())
        chat_queue_key = f"chat:{session_key}"
        msg_queue_key = f"session.message:{session_key}"
        chat_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_queues[chat_queue_key] = chat_queue
        self._event_queues[msg_queue_key] = msg_queue

        await self._rpc(
            "sessions.send",
            {
                "key": session_key,
                "message": message,
                "idempotencyKey": idempotency_key,
            },
        )

        collected_messages: list[TranscriptMessage] = []
        done = False
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            while not done:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    logger.warning("Timeout waiting for final state on session %s", session_key)
                    break
                try:
                    event = await asyncio.wait_for(chat_queue.get(), timeout=min(0.5, remaining))
                    state = event.get("payload", {}).get("state", "")
                    if state in {"final", "aborted", "error"}:
                        done = True
                except asyncio.TimeoutError:
                    pass

            collected_messages.extend(
                await _drain_message_queue(
                    msg_queue,
                    quiet_seconds=0.75,
                    max_wait_seconds=2.0,
                )
            )

            # Some gateway/provider paths persist assistant messages in session
            # history without emitting complete streaming events. Backfill from
            # sessions.get if stream capture appears incomplete.
            history_messages = await self.get_session_messages(session_key)
            collected_assistant = sum(
                1 for msg in collected_messages if msg.role == "assistant"
            )
            history_assistant = sum(
                1 for msg in history_messages if msg.role == "assistant"
            )
            if history_messages and (
                len(history_messages) > len(collected_messages)
                or history_assistant > collected_assistant
            ):
                collected_messages = history_messages
        finally:
            self._event_queues.pop(chat_queue_key, None)
            self._event_queues.pop(msg_queue_key, None)

        return _correlate_transcript(Transcript(messages=collected_messages))

    async def get_session_messages(self, session_key: str) -> list[TranscriptMessage]:
        try:
            response = await self._rpc("sessions.get", {"key": session_key})
        except Exception:
            return []

        payload = response.get("payload", {})
        raw_messages = payload.get("messages", [])
        if not isinstance(raw_messages, list):
            return []

        parsed: list[TranscriptMessage] = []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            msg = _parse_single_message(raw)
            if msg is not None:
                parsed.append(msg)
        return parsed

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self._ws:
            raise RuntimeError("Gateway client is not connected")

        request_id = str(uuid.uuid4())
        frame = {"type": "req", "id": request_id, "method": method}
        if params is not None:
            frame["params"] = params

        effective_timeout = timeout if timeout is not None else self.config.request_timeout
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._ws.send(json.dumps(frame))
        try:
            response = await asyncio.wait_for(future, timeout=effective_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise TimeoutError(
                f"RPC {method} timed out after {effective_timeout:.1f}s"
            )

        if not response.get("ok", False):
            error = response.get("error", {})
            raise RuntimeError(
                f"RPC {method} failed: {error.get('code', '?')} - {error.get('message', '')}"
            )
        return response

    async def _listener(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                frame_type = frame.get("type")
                if frame_type == "res":
                    request_id = frame.get("id", "")
                    future = self._pending.pop(request_id, None)
                    if future and not future.done():
                        future.set_result(frame)
                    continue

                if frame_type != "event":
                    continue

                event_name = frame.get("event", "")
                payload = frame.get("payload", {})
                session_key = payload.get("sessionKey", "")
                queue_key = f"{event_name}:{session_key}"
                if queue_key in self._event_queues:
                    await self._event_queues[queue_key].put(frame)
                if event_name not in self._event_queues:
                    self._event_queues[event_name] = asyncio.Queue()
                await self._event_queues[event_name].put(frame)
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket closed")
        except asyncio.CancelledError:
            pass

    async def _wait_event(self, event_name: str, *, timeout: float) -> dict[str, Any]:
        if event_name not in self._event_queues:
            self._event_queues[event_name] = asyncio.Queue()
        return await asyncio.wait_for(self._event_queues[event_name].get(), timeout=timeout)


def _build_connect_device(
    *,
    nonce: str,
    token: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    platform: str,
    device_family: str | None = None,
) -> dict[str, Any] | None:
    if not nonce:
        return None

    helper_input = json.dumps(
        {
            "nonce": nonce,
            "token": token or "",
            "clientId": client_id,
            "clientMode": client_mode,
            "role": role,
            "scopes": scopes,
            "platform": platform,
            "deviceFamily": device_family or "",
        }
    )

    node_executable = _resolve_node_executable()
    if not node_executable:
        logger.warning(
            "Failed to build device identity payload: no Node executable found"
        )
        return None

    try:
        completed = subprocess.run(
            [node_executable, "-e", DEVICE_IDENTITY_HELPER_JS],
            input=helper_input,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Failed to build device identity payload: %s", exc)
        return None

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse device identity payload: %s", exc)
        return None

    required_keys = {"id", "publicKey", "signature", "signedAt", "nonce"}
    if not isinstance(payload, dict) or not required_keys.issubset(payload):
        logger.warning("Device identity helper returned unexpected payload: %r", payload)
        return None
    return payload


def _resolve_node_executable() -> str | None:
    """Resolve Node binary, preferring the active Python/conda environment."""
    candidates: list[str] = []

    # First try the same environment as the active Python interpreter.
    candidates.append(os.path.join(os.path.dirname(sys.executable), "node"))

    # Then try CONDA_PREFIX when available.
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidates.append(os.path.join(conda_prefix, "bin", "node"))

    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return shutil.which("node")


def _is_transient_gateway_connect_error(exc: Exception) -> bool:
    if isinstance(exc, InvalidStatus):
        return exc.response.status_code in {502, 503, 504}
    if isinstance(exc, InvalidMessage):
        return "valid http response" in str(exc).lower()
    if isinstance(exc, OSError):
        return True
    return False


def _describe_connect_error(exc: Exception) -> str:
    if isinstance(exc, InvalidStatus):
        return f"HTTP {exc.response.status_code}"
    return exc.__class__.__name__


def _parse_single_message(message_data: dict[str, Any]) -> TranscriptMessage | None:
    role = message_data.get("role", "")
    if not role:
        return None

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    tool_result_for: str | None = None
    tool_result_content = ""
    usage = _parse_usage_payload(message_data.get("usage", {}))

    content = message_data.get("content", "")
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
                continue
            if block_type == "output_text":
                text_parts.append(block.get("text", ""))
                continue
            if block_type in {"tool_use", "toolCall"}:
                arguments = block.get("input", block.get("arguments", {}))
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {"raw": arguments}
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=arguments if isinstance(arguments, dict) else {},
                    )
                )
                continue
            if block_type in {"tool_result", "toolResult"}:
                tool_result_for = (
                    block.get("tool_use_id")
                    or block.get("toolCallId")
                    or block.get("tool_call_id")
                    or block.get("id")
                )
                tool_result_content = _flatten_tool_content(block.get("content", ""))
                if tool_result_content:
                    text_parts.append(tool_result_content)

    # Some providers surface assistant failures in a dedicated error field
    # with empty content blocks. Preserve that signal in transcript text.
    error_message = message_data.get("errorMessage", "")
    if isinstance(error_message, str) and error_message.strip():
        text_parts.append(error_message.strip())

    direct_text = message_data.get("text", "")
    if isinstance(direct_text, str) and direct_text.strip():
        text_parts.append(direct_text.strip())

    if not text_parts and not tool_calls and not tool_result_for:
        return None

    return TranscriptMessage(
        role=role,
        text="\n".join(part for part in text_parts if part),
        tool_calls=tool_calls,
        tool_result_for=tool_result_for,
        tool_result_content=tool_result_content,
        usage=usage,
    )


def _flatten_tool_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item["text"]))
                elif "content" in item:
                    parts.append(str(item["content"]))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)


async def _drain_message_queue(
    msg_queue: asyncio.Queue[dict[str, Any]],
    *,
    quiet_seconds: float,
    max_wait_seconds: float,
) -> list[TranscriptMessage]:
    collected: list[TranscriptMessage] = []
    loop = asyncio.get_running_loop()
    overall_deadline = loop.time() + max_wait_seconds
    quiet_deadline = loop.time() + quiet_seconds

    while True:
        while not msg_queue.empty():
            event = msg_queue.get_nowait()
            parsed = _parse_single_message(event.get("payload", {}).get("message", {}))
            if parsed:
                collected.append(parsed)
                quiet_deadline = loop.time() + quiet_seconds

        now = loop.time()
        if now >= overall_deadline or now >= quiet_deadline:
            break

        wait_time = min(0.1, overall_deadline - now, quiet_deadline - now)
        if wait_time <= 0:
            break
        try:
            event = await asyncio.wait_for(msg_queue.get(), timeout=wait_time)
        except asyncio.TimeoutError:
            continue
        parsed = _parse_single_message(event.get("payload", {}).get("message", {}))
        if parsed:
            collected.append(parsed)
            quiet_deadline = loop.time() + quiet_seconds

    return collected


def _parse_usage_payload(payload: Any) -> TokenUsage:
    if not isinstance(payload, dict):
        return TokenUsage()

    def _int_value(*keys: str) -> int:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    cost_payload = payload.get("cost", {})
    total_cost = 0.0
    if isinstance(cost_payload, dict):
        raw_total = cost_payload.get("total", cost_payload.get("usd", 0.0))
        if isinstance(raw_total, (int, float)):
            total_cost = float(raw_total)
    elif isinstance(cost_payload, (int, float)):
        total_cost = float(cost_payload)

    input_tokens = _int_value("input", "inputTokens")
    output_tokens = _int_value("output", "outputTokens")
    reasoning_tokens = _int_value("reasoning", "reasoningTokens", "outputReasoningTokens")
    cache_read_tokens = _int_value("cacheRead", "cache_read", "cacheReadTokens")
    cache_write_tokens = _int_value("cacheWrite", "cache_write", "cacheWriteTokens")
    total_tokens = _int_value("totalTokens", "total", "total_tokens")
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens + reasoning_tokens

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
    )


def _looks_like_error(text: str) -> bool:
    normalized = text.lower()
    error_patterns = [
        r"\berror\b",
        r"\bfailed\b",
        r"\bexception\b",
        r"\btraceback\b",
        r"\bnot found\b",
        r"\bno such file\b",
        r"\bpermission denied\b",
        r"\binvalid\b",
    ]
    return any(re.search(pattern, normalized) for pattern in error_patterns)


def _correlate_transcript(transcript: Transcript) -> Transcript:
    by_id: dict[str, ToolCall] = {}
    for message in transcript.messages:
        for tool_call in message.tool_calls:
            if tool_call.id:
                by_id[tool_call.id] = tool_call
        if message.tool_result_for and message.tool_result_for in by_id:
            tool_call = by_id[message.tool_result_for]
            if message.tool_result_content:
                if tool_call.output:
                    tool_call.output = f"{tool_call.output}\n{message.tool_result_content}".strip()
                else:
                    tool_call.output = message.tool_result_content
            if tool_call.success is None:
                tool_call.success = not _looks_like_error(tool_call.output)
            if tool_call.success is False and not tool_call.error:
                tool_call.error = tool_call.output

    for tool_call in transcript.tool_call_sequence:
        if tool_call.success is None and tool_call.output:
            tool_call.success = not _looks_like_error(tool_call.output)
        if tool_call.success is False and not tool_call.error:
            tool_call.error = tool_call.output
    return transcript
