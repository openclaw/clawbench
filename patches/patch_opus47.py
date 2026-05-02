#!/usr/bin/env python3
"""Patch pi-ai and openclaw bundles to recognize claude-opus-4-7 (and sonnet-4-7).

Runs inside the Docker image as a RUN step. Idempotent: re-running is a no-op.
"""

import re
import sys
import os

PI_AI_CATALOG = "/app/node_modules/@mariozechner/pi-ai/dist/models.generated.js"
ANTHROPIC_REGISTER_GLOB = "/app/dist/register.runtime-*.js"


def patch_pi_ai_catalog(path: str) -> bool:
    with open(path) as fh:
        src = fh.read()
    if '"claude-opus-4-7"' in src:
        print(f"[patch] {path}: claude-opus-4-7 already present, skipping")
        return False

    # Find the claude-opus-4-6 entry and splice in opus-4-7 + sonnet-4-7 right after.
    # Use substring scanning rather than regex because each entry contains a nested
    # `cost: { ... }` object (which breaks naive `[^{}]` patterns).
    start_marker = '"claude-opus-4-6": {'
    start_idx = src.find(start_marker)
    if start_idx == -1:
        print(f"[patch] ERROR: could not locate claude-opus-4-6 anchor in {path}", file=sys.stderr)
        sys.exit(1)
    # Walk forward from the opening `{` counting nesting until it balances to 0.
    depth = 0
    i = start_idx
    while i < len(src):
        ch = src[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                i += 1  # include '}'
                break
        i += 1
    if depth != 0:
        print(f"[patch] ERROR: unbalanced braces walking claude-opus-4-6 entry in {path}", file=sys.stderr)
        sys.exit(1)
    # There should be a trailing comma after the closing brace.
    if i < len(src) and src[i] == ',':
        i += 1
    anchor_end = i

    class _M:
        def __init__(self, end): self._end = end
        def end(self): return self._end
    m = _M(anchor_end)

    insertion = (
        "\n"
        '        "claude-opus-4-7": {\n'
        '            id: "claude-opus-4-7",\n'
        '            name: "Claude Opus 4.7",\n'
        '            api: "anthropic-messages",\n'
        '            provider: "anthropic",\n'
        '            baseUrl: "https://api.anthropic.com",\n'
        "            reasoning: true,\n"
        '            input: ["text", "image"],\n'
        "            cost: {\n"
        "                input: 5,\n"
        "                output: 25,\n"
        "                cacheRead: 0.5,\n"
        "                cacheWrite: 6.25,\n"
        "            },\n"
        "            contextWindow: 1000000,\n"
        "            maxTokens: 128000,\n"
        "        },\n"
        '        "claude-sonnet-4-7": {\n'
        '            id: "claude-sonnet-4-7",\n'
        '            name: "Claude Sonnet 4.7",\n'
        '            api: "anthropic-messages",\n'
        '            provider: "anthropic",\n'
        '            baseUrl: "https://api.anthropic.com",\n'
        "            reasoning: true,\n"
        '            input: ["text", "image"],\n'
        "            cost: {\n"
        "                input: 3,\n"
        "                output: 15,\n"
        "                cacheRead: 0.3,\n"
        "                cacheWrite: 3.75,\n"
        "            },\n"
        "            contextWindow: 1000000,\n"
        "            maxTokens: 128000,\n"
        "        },"
    )

    patched = src[: m.end()] + insertion + src[m.end():]
    with open(path, "w") as fh:
        fh.write(patched)
    print(f"[patch] {path}: inserted claude-opus-4-7 and claude-sonnet-4-7")
    return True


def patch_openclaw_anthropic_register(path: str) -> bool:
    with open(path) as fh:
        src = fh.read()
    if "ANTHROPIC_OPUS_47_MODEL_ID" in src:
        print(f"[patch] {path}: 4-7 support already present, skipping")
        return False

    # Skip files that are not the anthropic register.runtime (other plugins
    # share the same `register.runtime-*.js` naming convention).
    if 'PROVIDER_ID = "anthropic"' not in src or "ANTHROPIC_MODERN_MODEL_PREFIXES" not in src:
        print(f"[patch] {path}: not the anthropic register.runtime bundle, skipping")
        return False

    # 1. Inject new constants after the sonnet template constant.
    sonnet_tpl_anchor = 'const ANTHROPIC_SONNET_TEMPLATE_MODEL_IDS = ["claude-sonnet-4-5", "claude-sonnet-4.5"];'
    if sonnet_tpl_anchor not in src:
        print(f"[patch] ERROR: sonnet template anchor not found in {path}", file=sys.stderr)
        sys.exit(1)
    new_consts = (
        sonnet_tpl_anchor + "\n"
        'const ANTHROPIC_OPUS_47_MODEL_ID = "claude-opus-4-7";\n'
        'const ANTHROPIC_OPUS_47_DOT_MODEL_ID = "claude-opus-4.7";\n'
        'const ANTHROPIC_SONNET_47_MODEL_ID = "claude-sonnet-4-7";\n'
        'const ANTHROPIC_SONNET_47_DOT_MODEL_ID = "claude-sonnet-4.7";'
    )
    src = src.replace(sonnet_tpl_anchor, new_consts)

    # 2. Extend ANTHROPIC_MODERN_MODEL_PREFIXES.
    prefixes_anchor = 'const ANTHROPIC_MODERN_MODEL_PREFIXES = [\n\t"claude-opus-4-6",\n\t"claude-sonnet-4-6",'
    prefixes_new = 'const ANTHROPIC_MODERN_MODEL_PREFIXES = [\n\t"claude-opus-4-7",\n\t"claude-sonnet-4-7",\n\t"claude-opus-4-6",\n\t"claude-sonnet-4-6",'
    if prefixes_anchor not in src:
        print(f"[patch] ERROR: modern prefixes anchor not found in {path}", file=sys.stderr)
        sys.exit(1)
    src = src.replace(prefixes_anchor, prefixes_new)

    # 3. Add 4-7 forward-compat branches ahead of the 4-6 opus/sonnet branches.
    resolve_anchor = (
        "function resolveAnthropicForwardCompatModel(ctx) {\n"
        "\treturn resolveAnthropic46ForwardCompatModel({\n"
        "\t\tctx,\n"
        "\t\tdashModelId: ANTHROPIC_OPUS_46_MODEL_ID,"
    )
    resolve_new = (
        "function resolveAnthropicForwardCompatModel(ctx) {\n"
        "\treturn resolveAnthropic46ForwardCompatModel({\n"
        "\t\tctx,\n"
        '\t\tdashModelId: ANTHROPIC_OPUS_47_MODEL_ID,\n'
        '\t\tdotModelId: ANTHROPIC_OPUS_47_DOT_MODEL_ID,\n'
        '\t\tdashTemplateId: "claude-opus-4-6",\n'
        '\t\tdotTemplateId: "claude-opus-4.6",\n'
        "\t\tfallbackTemplateIds: ANTHROPIC_OPUS_TEMPLATE_MODEL_IDS\n"
        "\t}) ?? resolveAnthropic46ForwardCompatModel({\n"
        "\t\tctx,\n"
        '\t\tdashModelId: ANTHROPIC_SONNET_47_MODEL_ID,\n'
        '\t\tdotModelId: ANTHROPIC_SONNET_47_DOT_MODEL_ID,\n'
        '\t\tdashTemplateId: "claude-sonnet-4-6",\n'
        '\t\tdotTemplateId: "claude-sonnet-4.6",\n'
        "\t\tfallbackTemplateIds: ANTHROPIC_SONNET_TEMPLATE_MODEL_IDS\n"
        "\t}) ?? resolveAnthropic46ForwardCompatModel({\n"
        "\t\tctx,\n"
        "\t\tdashModelId: ANTHROPIC_OPUS_46_MODEL_ID,"
    )
    if resolve_anchor not in src:
        print(f"[patch] ERROR: forward-compat resolver anchor not found in {path}", file=sys.stderr)
        sys.exit(1)
    src = src.replace(resolve_anchor, resolve_new)

    # 4. Make adaptive-thinking default cover 4-7 too.
    adaptive_anchor = (
        "function shouldUseAnthropicAdaptiveThinkingDefault(modelId) {\n"
        "\tconst lowerModelId = normalizeLowercaseStringOrEmpty(modelId);\n"
        "\treturn lowerModelId.startsWith(ANTHROPIC_OPUS_46_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_OPUS_46_DOT_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_SONNET_46_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_SONNET_46_DOT_MODEL_ID);\n"
        "}"
    )
    adaptive_new = (
        "function shouldUseAnthropicAdaptiveThinkingDefault(modelId) {\n"
        "\tconst lowerModelId = normalizeLowercaseStringOrEmpty(modelId);\n"
        "\treturn lowerModelId.startsWith(ANTHROPIC_OPUS_47_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_OPUS_47_DOT_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_SONNET_47_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_SONNET_47_DOT_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_OPUS_46_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_OPUS_46_DOT_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_SONNET_46_MODEL_ID) || lowerModelId.startsWith(ANTHROPIC_SONNET_46_DOT_MODEL_ID);\n"
        "}"
    )
    if adaptive_anchor in src:
        src = src.replace(adaptive_anchor, adaptive_new)

    with open(path, "w") as fh:
        fh.write(src)
    print(f"[patch] {path}: added claude-opus-4-7 / claude-sonnet-4-7 forward-compat support")
    return True


def main() -> None:
    import glob

    any_changed = False
    if os.path.exists(PI_AI_CATALOG):
        any_changed |= patch_pi_ai_catalog(PI_AI_CATALOG)
    else:
        print(f"[patch] WARNING: {PI_AI_CATALOG} not found", file=sys.stderr)

    candidates = sorted(glob.glob(ANTHROPIC_REGISTER_GLOB))
    if not candidates:
        print(f"[patch] WARNING: no files match {ANTHROPIC_REGISTER_GLOB}", file=sys.stderr)
    for cand in candidates:
        any_changed |= patch_openclaw_anthropic_register(cand)

    if any_changed:
        print("[patch] success")
    else:
        print("[patch] no changes applied (already patched)")


if __name__ == "__main__":
    main()
