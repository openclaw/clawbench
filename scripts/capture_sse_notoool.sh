#!/bin/bash
# Second SSE capture — this time with a prompt that yields a plain TEXT
# response (no tool_calls), to trigger the finish_reason="stop" path that
# is what the "incomplete turn detected: stopReason=stop payloads=0" bug
# reports on.

set -u

OUTDIR="/data/sse_captures"
mkdir -p "$OUTDIR"

KEY=$(python3 -c 'import json; print(json.load(open("/home/node/.openclaw/openclaw.json"))["env"]["OPENROUTER_API_KEY"])')

body_of() {
  local model="$1"
  cat <<EOF
{
  "model": "$model",
  "stream": true,
  "max_tokens": 400,
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Briefly explain what a semaphore is in one paragraph."}
  ]
}
EOF
}

for entry in "z-ai/glm-5.1:zai_text" "minimax/minimax-m2.7:minimax_text" "moonshotai/kimi-k2.5:moonshotai_text"; do
  IFS=':' read -r model label <<< "$entry"
  out="$OUTDIR/$label.sse"
  echo "=== $label ($model) ==="
  curl -sS -N --max-time 90 \
    -X POST https://openrouter.ai/api/v1/chat/completions \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -d "$(body_of "$model")" \
    > "$out" 2>&1
  echo "  saved $(wc -l < $out) lines, $(grep -c '^data: ' $out 2>/dev/null) data events"
done

echo ""
echo "=== text-path summary ==="
python3 << 'PY'
import json, os, collections

OUTDIR = "/data/sse_captures"
for label in ["zai_text", "minimax_text", "moonshotai_text"]:
    path = f"{OUTDIR}/{label}.sse"
    if not os.path.exists(path):
        continue
    print(f"\n--- {label} ---")
    delta_keys = collections.Counter()
    content_nonempty = 0
    content_total = 0
    reasoning_total = 0
    finish_reasons = collections.Counter()
    first_content_sample = None
    last_chunk = None
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
        last_chunk = j
        for ch in j.get("choices", []):
            if "delta" in ch and isinstance(ch["delta"], dict):
                d = ch["delta"]
                for k in d.keys():
                    delta_keys[k] += 1
                c = d.get("content")
                content_total += 1
                if c not in (None, ""):
                    content_nonempty += 1
                    if first_content_sample is None:
                        first_content_sample = c[:80]
                if d.get("reasoning") or d.get("reasoning_details"):
                    reasoning_total += 1
            if ch.get("finish_reason"):
                finish_reasons[ch["finish_reason"]] += 1
    print(f"  delta keys: {dict(delta_keys.most_common())}")
    print(f"  content: {content_nonempty}/{content_total} chunks non-empty")
    print(f"  reasoning: {reasoning_total} chunks with reasoning field")
    print(f"  finish_reasons: {dict(finish_reasons)}")
    print(f"  first content: {first_content_sample!r}")
    if last_chunk:
        print(f"  last chunk: {json.dumps(last_chunk)[:300]}")
PY
