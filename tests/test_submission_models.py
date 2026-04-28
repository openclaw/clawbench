from clawbench.submission_models import (
    build_preset_submission_specs,
    CUSTOM_PRESET_LABEL,
    PRESET_AUDIENCE_BUDGET,
    PRESET_AUDIENCE_CLAW,
    infer_provider,
    preset_labels_for_audience,
    resolve_model_selection,
)


def test_budget_audience_keeps_budget_friendly_presets():
    labels = preset_labels_for_audience(PRESET_AUDIENCE_BUDGET)

    assert "GPT-OSS 20B (Ollama)" in labels
    assert "Qwen 3.5 27B (Ollama)" in labels
    assert "Claude Opus 4.6" not in labels


def test_claw_audience_keeps_full_catalog():
    labels = preset_labels_for_audience(PRESET_AUDIENCE_CLAW)

    assert "GPT-OSS 20B (Ollama)" in labels
    assert "Claude Opus 4.6" in labels


def test_resolve_model_selection_prefers_preset_provider():
    model_id, provider = resolve_model_selection("", "GPT-OSS 20B (Ollama)")

    assert model_id == "ollama/gpt-oss:20b"
    assert provider == "ollama"


def test_resolve_model_selection_overrides_stale_provider_for_preset():
    model_id, provider = resolve_model_selection(
        "",
        "GPT-OSS 20B (Ollama)",
        "anthropic",
    )

    assert model_id == "ollama/gpt-oss:20b"
    assert provider == "ollama"


def test_resolve_model_selection_infers_custom_provider():
    model_id, provider = resolve_model_selection(
        "huggingface/Qwen/Qwen3-32B",
        CUSTOM_PRESET_LABEL,
    )

    assert model_id == "huggingface/Qwen/Qwen3-32B"
    assert provider == "huggingface"


def test_infer_provider_requires_provider_prefix():
    assert infer_provider("qwen3.5:27b") == ""


def test_build_preset_submission_specs_preserves_selected_settings():
    specs = build_preset_submission_specs(
        PRESET_AUDIENCE_BUDGET,
        runs=4,
        max_parallel_lanes=2,
        judge_model=" anthropic/claude-opus-4-6 ",
        tier="tier3",
        scenario="coding_dev_assist",
        prompt_variant="ambiguous",
        submitter=" tester ",
    )

    assert specs
    preset, request_kwargs = specs[0]
    assert preset.provider == request_kwargs["provider"]
    assert request_kwargs["judge_model"] == "anthropic/claude-opus-4-6"
    assert request_kwargs["runs_per_task"] == 4
    assert request_kwargs["max_parallel_lanes"] == 2
    assert request_kwargs["tier"] == "tier3"
    assert request_kwargs["scenario"] == "coding_dev_assist"
    assert request_kwargs["prompt_variant"] == "ambiguous"
    assert request_kwargs["submitter"] == "tester"
