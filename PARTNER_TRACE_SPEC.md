# Partner Trace Spec

## Purpose

This document defines the preferred trace format for industry partners contributing real agent runs to ClawBench.

The goal is not only to capture what the agent did, but also the full execution context needed to:

- compare runs fairly across partners and harnesses
- recover task structure and tool usage
- study failure modes and behavior patterns
- preserve enough provenance for reproducibility and auditing

## File Format

- Preferred format: `JSONL` encoded as UTF-8
- One trace record per line
- Each line must be a single valid JSON object
- Small batches may also be shared as a JSON array of objects, but JSONL is preferred for streaming and incremental ingestion
- Timestamps should be in UTC using ISO 8601 when represented as strings

## Top-Level Record Shape

Each trace record should have this top-level structure:

```json
{
  "trace_id": "partner-2026-04-11-000123",
  "created_at": "2026-04-11T18:22:03Z",
  "partner_name": "acme",
  "privacy_tier": "partner_restricted",
  "harness": {},
  "model": {},
  "config": {},
  "plugins": [],
  "skills": [],
  "prompts": {},
  "task_metadata": {},
  "transcript": {
    "messages": []
  },
  "artifacts": {},
  "redaction": {},
  "metadata": {}
}
```

## Required Fields

These fields should always be present:

- `trace_id`: stable unique identifier for the run
- `created_at`: trace creation time in UTC
- `partner_name`: partner or source label
- `harness.type`: harness family or runner type
- `harness.version`: harness version used for the run
- `model.provider`: model provider
- `model.name`: model name
- `config`: effective runtime configuration for the run
- `plugins`: plugins or tool bundles available to the agent, even if empty
- `prompts.user`: the user task or user-visible request
- `task_metadata`: benchmark task axes, when the trace corresponds to a ClawBench task
- `transcript.messages`: ordered message list for the run

## Strongly Recommended Fields

These materially improve trace quality and downstream usefulness:

- full prompt stack, including `system`, `developer`, and wrapper prompts
- plugin versions and stable plugin identifiers
- skill versions and stable skill identifiers
- tool call outputs and error details
- token and cost usage
- repo SHA, container image digest, or equivalent execution fingerprint
- redaction metadata describing what was removed or transformed
- final artifacts, exit codes, and test results

## Metadata We Want

### 1. Task Metadata

When a trace maps to a benchmark task, include the same flat task axes
used by ClawBench result aggregation. These axes are intentionally
orthogonal and harness-neutral; do not nest them under an agent product
or plugin stack.

Recommended fields:

```json
{
  "task_id": "t4-browser-research-and-code",
  "category": "software_engineering",
  "domain": "devtools",
  "functionality": ["browser_research", "api_contract_extraction", "code_repair"],
  "trace_distribution": ["browser_heavy", "read_heavy", "edit_heavy", "execute_heavy"],
  "tool_surface": ["browser", "filesystem", "shell", "local_service"],
  "risk_tags": ["code_regression", "hallucination"]
}
```

### 2. Harness

Use `harness` to describe the execution framework itself.

Recommended fields:

```json
{
  "type": "openclaw",
  "name": "OpenClaw Desktop",
  "version": "0.5.0",
  "git_sha": "abc123",
  "image_digest": "sha256:...",
  "os": "macos-15.4",
  "runtime": "python-3.12",
  "invocation": "benchmark",
  "entrypoint": "codex-desktop"
}
```

### 3. Model

Use `model` to identify the model under test.

Recommended fields:

```json
{
  "provider": "openai",
  "name": "gpt-5.4",
  "snapshot": "gpt-5.4-2026-04-01",
  "api_mode": "responses",
  "reasoning_effort": "medium"
}
```

### 4. Config

Use `config` for the effective runtime settings that could change behavior.

Recommended fields:

```json
{
  "max_turns": 40,
  "timeout_seconds": 900,
  "approval_mode": "never",
  "sandbox_mode": "danger-full-access",
  "tool_policy": "default",
  "context_window": 256000,
  "temperature": 0.2,
  "top_p": 1.0,
  "parallel_tool_calls": true,
  "retry_policy": "default"
}
```

If a field is unavailable, omit it rather than inventing a value.

### 5. Plugins

Use `plugins` for tools, plugin bundles, MCP servers, extensions, or other agent capabilities exposed by the harness.

Each entry should ideally include:

- stable plugin id
- human-readable name
- version
- source or package origin
- manifest hash or equivalent fingerprint
- enabled/disabled status
- tool names exposed by the plugin

Recommended entry shape:

```json
{
  "id": "browser-playwright",
  "name": "Playwright Browser",
  "version": "1.2.3",
  "source": "internal",
  "manifest_hash": "sha256:...",
  "enabled": true,
  "tools": ["browser_navigate", "browser_click", "browser_type"]
}
```

### 6. Skills

Use `skills` for reusable instruction bundles, templates, internal playbooks, or any named capability layer available to the agent.

Each skill entry should ideally include:

- stable skill id or name
- version or revision
- source
- whether it was available in the run
- whether it was actually invoked or referenced

Recommended entry shape:

```json
{
  "id": "openai-docs",
  "version": "2026-04-01",
  "source": "local-skill",
  "available": true,
  "invoked": false
}
```

### 7. Prompts

Use `prompts` for the prompt stack that shaped agent behavior.

Recommended fields:

```json
{
  "system": "raw or redacted system prompt",
  "developer": "raw or redacted developer prompt",
  "user": "user-visible request",
  "tooling_wrapper": "optional wrapper prompt",
  "skills_wrapper": "optional skill-injection prompt"
}
```

If raw prompts cannot be shared, provide one of:

- redacted prompt text, or
- stable hashes plus short labels

Example:

```json
{
  "system_hash": "sha256:...",
  "developer_hash": "sha256:...",
  "user": "Fix the search bug and keep the tests green."
}
```

### 8. Transcript

`transcript.messages` is the core behavioral record.

Each message should preserve order and include:

- `role`
- `text`
- `timestamp_ms` when available
- `tool_calls` for assistant messages that invoked tools
- `usage` when available

Recommended message shape:

```json
{
  "role": "assistant",
  "text": "I am going to inspect the search flow and run the tests.",
  "timestamp_ms": 1712869325000,
  "tool_calls": [
    {
      "id": "tc_1",
      "name": "read_file",
      "input": {"path": "src/search.js"},
      "output": "optional raw or truncated tool output",
      "success": true,
      "timestamp_ms": 1712869327000,
      "family": "filesystem",
      "mutating": false,
      "error": ""
    }
  ],
  "usage": {
    "input_tokens": 210,
    "output_tokens": 48,
    "reasoning_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "total_tokens": 258,
    "total_cost_usd": 0.0019
  }
}
```

### 8. Artifacts

Use `artifacts` to summarize concrete outputs of the run.

Recommended fields:

```json
{
  "final_status": "pass",
  "final_message": "Patched the bug and tests pass.",
  "files_written": ["src/search.js"],
  "files_modified": ["src/search.js", "test/search.test.js"],
  "commands": [
    {"cmd": "npm test", "exit_code": 0}
  ],
  "tests": [
    {"name": "npm test", "exit_code": 0, "passed": true}
  ]
}
```

### 9. Redaction

Use `redaction` to describe privacy filtering applied before sharing.

Recommended fields:

```json
{
  "applied": true,
  "policy": "partner-v1",
  "notes": "Emails and absolute paths were masked.",
  "fields_removed": ["prompts.system", "transcript.messages[].tool_calls[].output"]
}
```

### 10. Free-Form Metadata

Use `metadata` for additional context that does not fit cleanly elsewhere.

Examples:

- `session_id`
- `workspace_kind`
- `repo_language`
- `task_category`
- `trace_completeness`
- `customer_segment`

## Canonical Example

```json
{
  "trace_id": "partner-2026-04-11-000123",
  "created_at": "2026-04-11T18:22:03Z",
  "partner_name": "acme",
  "privacy_tier": "partner_restricted",
  "harness": {
    "type": "openclaw",
    "name": "OpenClaw Desktop",
    "version": "0.5.0",
    "git_sha": "abc123"
  },
  "model": {
    "provider": "openai",
    "name": "gpt-5.4",
    "snapshot": "gpt-5.4-2026-04-01"
  },
  "config": {
    "max_turns": 40,
    "timeout_seconds": 900,
    "approval_mode": "never",
    "sandbox_mode": "danger-full-access"
  },
  "plugins": [
    {
      "id": "browser-playwright",
      "name": "Playwright Browser",
      "version": "1.2.3",
      "enabled": true,
      "tools": ["browser_navigate", "browser_click"]
    }
  ],
  "skills": [
    {
      "id": "openai-docs",
      "version": "2026-04-01",
      "source": "local-skill",
      "available": true,
      "invoked": false
    }
  ],
  "prompts": {
    "system": "raw or redacted system prompt",
    "developer": "raw or redacted developer prompt",
    "user": "Search is off somewhere in this Node app. Trace it through the files, fix it, and keep the tests green."
  },
  "transcript": {
    "messages": [
      {
        "role": "user",
        "text": "Search is off somewhere in this Node app. Trace it through the files, fix it, and keep the tests green.",
        "timestamp_ms": 1712869323000
      },
      {
        "role": "assistant",
        "text": "I am going to inspect the search flow and run the tests.",
        "timestamp_ms": 1712869325000,
        "tool_calls": [
          {
            "id": "tc_1",
            "name": "read_file",
            "input": {"path": "src/search.js"},
            "success": true,
            "timestamp_ms": 1712869327000,
            "family": "filesystem",
            "mutating": false
          },
          {
            "id": "tc_2",
            "name": "exec_command",
            "input": {"cmd": "npm test"},
            "success": true,
            "timestamp_ms": 1712869331000,
            "family": "shell",
            "mutating": false
          }
        ],
        "usage": {
          "input_tokens": 210,
          "output_tokens": 48,
          "total_tokens": 258,
          "total_cost_usd": 0.0019
        }
      }
    ]
  },
  "artifacts": {
    "final_status": "pass",
    "files_modified": ["src/search.js"],
    "tests": [
      {"name": "npm test", "exit_code": 0, "passed": true}
    ]
  },
  "redaction": {
    "applied": true,
    "policy": "partner-v1"
  },
  "metadata": {
    "session_id": "sess_abc",
    "workspace_kind": "repo",
    "repo_language": "javascript",
    "trace_completeness": "full"
  }
}
```

## Minimum Compatible Payload

The current ClawBench ingest path can work with a smaller payload, but this should be treated as the minimum fallback rather than the target partner format.

```json
{
  "trace_id": "trace-001",
  "created_at": "2026-04-11T18:22:03Z",
  "partner_name": "acme",
  "user_prompt": "Search is off somewhere in this Node app.",
  "transcript": {
    "messages": [
      {"role": "user", "text": "Search is off somewhere in this Node app."},
      {
        "role": "assistant",
        "tool_calls": [
          {"name": "read_file", "input": {"path": "src/search.js"}, "success": true},
          {"name": "exec_command", "input": {"cmd": "npm test"}, "success": true}
        ]
      }
    ]
  }
}
```

## Guidance for Partners

- Preserve original ordering of messages and tool calls
- Do not drop failed tool calls
- Include redaction metadata whenever content is removed or transformed
- Prefer full prompts over hashes when policy allows
- Prefer explicit plugin and skill versions over generic labels
- If a field is unknown, omit it or set it to `null`; do not guess

## Summary

For partner submissions, we want traces that include:

- harness type, version, and execution fingerprint
- model identity
- effective config
- plugins and available tools
- skills and whether they were invoked
- prompt stack
- full transcript with tool calls
- final artifacts and outcomes
- redaction and provenance metadata

JSONL should be the default interchange format.
