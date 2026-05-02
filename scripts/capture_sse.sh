#!/bin/bash
# Capture raw SSE from OpenRouter for the 3 problematic upstreams.
# Run INSIDE a clawbench container (so it has the key).
#
# Saves raw streams to /data/sse_captures/*.sse and a field-shape summary
# to /data/sse_captures/summary.txt.

set -u

OUTDIR="/data/sse_captures"
mkdir -p "$OUTDIR"

KEY=$(python3 -c 'import json; print(json.load(open("/home/node/.openclaw/openclaw.json"))["env"]["OPENROUTER_API_KEY"])')

# Same minimal call body for all 3 — prompt that will trigger reasoning + a
# tool that forces a tool_call so we can observe tool streaming shape too.
body_of() {
  local model="$1"
  cat <<EOF
{
  "model": "$model",
  "stream": true,
  "max_tokens": 800,
  "messages": [
    {"role": "system", "content": "You are a helpful assistant. Use tools when available."},
    {"role": "user", "content": "What is 17 * 23? Use the multiply tool."}
  ],
  "tools": [{
    "type": "function",
    "function": {
      "name": "multiply",
      "description": "Multiply two integers",
      "parameters": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a","b"]
      }
    }
  }]
}
EOF
}

for entry in "z-ai/glm-5.1:zai" "minimax/minimax-m2.7:minimax" "moonshotai/kimi-k2.5:moonshotai"; do
  IFS=':' read -r model label <<< "$entry"
  out="$OUTDIR/$label.sse"
  echo "=== $label ($model) ==="
  curl -sS -N --max-time 120 \
    -X POST https://openrouter.ai/api/v1/chat/completions \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -d "$(body_of "$model")" \
    > "$out" 2>&1
  lines=$(wc -l < "$out")
  data_events=$(grep -c "^data: " "$out" 2>/dev/null || echo 0)
  echo "  saved $lines lines, $data_events data events"
done

echo ""
echo "=== per-stream field-shape summary ==="
python3 << 'PY'
import json, os, re, collections

OUTDIR = "/data/sse_captures"

for label in ["zai", "minimax", "moonshotai"]:
    path = f"{OUTDIR}/{label}.sse"
    if not os.path.exists(path):
        continue
    print(f"\n--- {label} ---")
    delta_keys = collections.Counter()
    message_keys = collections.Counter()
    reasoning_shapes = set()
    tool_call_shapes = set()
    content_shapes = set()
    finish_reasons = set()
    sample_delta = None
    sample_message = None
    sample_reasoning_details = None
    n_chunks = 0
    for line in open(path):
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            continue
        try:
            j = json.loads(payload)
        except json.JSONDecodeError:
            continue
        n_chunks += 1
        for ch in j.get("choices", []):
            if "delta" in ch and isinstance(ch["delta"], dict):
                d = ch["delta"]
                for k in d.keys():
                    delta_keys[k] += 1
                if d.get("content") is not None and sample_delta is None:
                    sample_delta = d
                if d.get("reasoning_details"):
                    reasoning_shapes.add(("delta.reasoning_details", type(d["reasoning_details"]).__name__))
                    if sample_reasoning_details is None:
                        sample_reasoning_details = d["reasoning_details"]
                if d.get("reasoning"):
                    reasoning_shapes.add(("delta.reasoning", type(d["reasoning"]).__name__))
                if d.get("reasoning_content"):
                    reasoning_shapes.add(("delta.reasoning_content", type(d["reasoning_content"]).__name__))
                if d.get("tool_calls"):
                    tc = d["tool_calls"]
                    tool_call_shapes.add(("delta.tool_calls", type(tc).__name__))
                    if isinstance(tc, list) and tc:
                        for k in tc[0].keys() if isinstance(tc[0], dict) else []:
                            tool_call_shapes.add(("delta.tool_calls[0]."+k,))
                if d.get("content") is not None:
                    content_shapes.add(("delta.content", type(d["content"]).__name__))
            if "message" in ch and isinstance(ch["message"], dict):
                m = ch["message"]
                for k in m.keys():
                    message_keys[k] += 1
                if sample_message is None:
                    sample_message = m
                if m.get("content") is not None:
                    content_shapes.add(("message.content", type(m["content"]).__name__))
                if m.get("reasoning_details"):
                    reasoning_shapes.add(("message.reasoning_details", type(m["reasoning_details"]).__name__))
                if m.get("tool_calls"):
                    tool_call_shapes.add(("message.tool_calls", type(m["tool_calls"]).__name__))
            if ch.get("finish_reason"):
                finish_reasons.add(ch["finish_reason"])
    print(f"  chunks: {n_chunks}")
    print(f"  delta key counts: {dict(delta_keys.most_common())}")
    print(f"  message key counts: {dict(message_keys.most_common())}")
    print(f"  content shapes: {content_shapes}")
    print(f"  reasoning shapes: {reasoning_shapes}")
    print(f"  tool_call shapes: {tool_call_shapes}")
    print(f"  finish_reasons: {finish_reasons}")
    if sample_reasoning_details is not None:
        s = json.dumps(sample_reasoning_details, indent=2)[:800]
        print(f"  sample reasoning_details: {s}")
    if sample_message is not None:
        s = json.dumps(sample_message, indent=2)[:800]
        print(f"  sample message: {s}")
PY
