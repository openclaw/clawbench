"""Preset model catalog and selection helpers for the Space submit UI."""

from __future__ import annotations

from dataclasses import dataclass

CUSTOM_PRESET_LABEL = "(custom)"

PRESET_AUDIENCE_ALL = "All Presets"
PRESET_AUDIENCE_CLAW = "Claw Users"
PRESET_AUDIENCE_BUDGET = "Budget Researchers"

PRESET_AUDIENCE_CHOICES = (
    PRESET_AUDIENCE_ALL,
    PRESET_AUDIENCE_CLAW,
    PRESET_AUDIENCE_BUDGET,
)


@dataclass(frozen=True)
class PresetModel:
    label: str
    model_id: str
    provider: str
    audiences: tuple[str, ...]


PRESET_MODELS = (
    PresetModel(
        label="GPT-OSS 20B (Ollama)",
        model_id="ollama/gpt-oss:20b",
        provider="ollama",
        audiences=(PRESET_AUDIENCE_CLAW, PRESET_AUDIENCE_BUDGET),
    ),
    PresetModel(
        label="Qwen 3.5 27B (Ollama)",
        model_id="ollama/qwen3.5:27b",
        provider="ollama",
        audiences=(PRESET_AUDIENCE_CLAW, PRESET_AUDIENCE_BUDGET),
    ),
    PresetModel(
        label="Qwen3 32B",
        model_id="huggingface/Qwen/Qwen3-32B",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW, PRESET_AUDIENCE_BUDGET),
    ),
    PresetModel(
        label="Gemma 4 26B MoE",
        model_id="huggingface/google/gemma-4-26B-A4B-it",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW, PRESET_AUDIENCE_BUDGET),
    ),
    PresetModel(
        label="GLM 5.1 (754B MoE)",
        model_id="huggingface/zai-org/GLM-5.1",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
    PresetModel(
        label="GLM 5 (400B MoE)",
        model_id="huggingface/zai-org/GLM-5",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
    PresetModel(
        label="DeepSeek R1",
        model_id="huggingface/deepseek-ai/DeepSeek-R1",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
    PresetModel(
        label="Kimi K2 Instruct",
        model_id="huggingface/moonshotai/Kimi-K2-Instruct",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
    PresetModel(
        label="MiniMax M2.5",
        model_id="huggingface/MiniMaxAI/MiniMax-M2.5",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
    PresetModel(
        label="Llama 3.3 70B",
        model_id="huggingface/meta-llama/Llama-3.3-70B-Instruct",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
    PresetModel(
        label="Llama 3.1 70B",
        model_id="huggingface/meta-llama/Llama-3.1-70B-Instruct",
        provider="huggingface",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
    PresetModel(
        label="Claude Sonnet 4.6",
        model_id="anthropic/claude-sonnet-4-6",
        provider="anthropic",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
    PresetModel(
        label="Claude Opus 4.6",
        model_id="anthropic/claude-opus-4-6",
        provider="anthropic",
        audiences=(PRESET_AUDIENCE_CLAW,),
    ),
)

PRESET_MODEL_MAP = {preset.label: preset.model_id for preset in PRESET_MODELS}
_PRESET_BY_LABEL = {preset.label: preset for preset in PRESET_MODELS}


def infer_provider(model_id: str) -> str:
    normalized = model_id.strip()
    if not normalized or "/" not in normalized:
        return ""
    return normalized.split("/", 1)[0].strip().lower()


def preset_models_for_audience(audience: str | None) -> list[PresetModel]:
    if not audience or audience == PRESET_AUDIENCE_ALL:
        return list(PRESET_MODELS)
    return [preset for preset in PRESET_MODELS if audience in preset.audiences]


def preset_labels_for_audience(audience: str | None) -> list[str]:
    return [preset.label for preset in preset_models_for_audience(audience)]


def build_preset_submission_specs(
    audience: str | None,
    *,
    runs: int,
    max_parallel_lanes: int,
    submitter: str,
    judge_model: str = "",
    tier: str | None = None,
    scenario: str | None = None,
    prompt_variant: str = "clear",
) -> list[tuple[PresetModel, dict[str, object]]]:
    """Return per-preset SubmissionRequest kwargs for the selected audience."""
    normalized_submitter = submitter.strip()
    normalized_judge_model = judge_model.strip()
    return [
        (
            preset,
            {
                "model": preset.model_id,
                "provider": preset.provider,
                "judge_model": normalized_judge_model,
                "runs_per_task": int(runs),
                "max_parallel_lanes": int(max_parallel_lanes),
                "tier": tier,
                "scenario": scenario,
                "prompt_variant": prompt_variant,
                "submitter": normalized_submitter,
            },
        )
        for preset in preset_models_for_audience(audience)
    ]


def resolve_model_selection(
    model: str,
    preset_label: str,
    provider: str = "",
) -> tuple[str, str]:
    selected_model = model.strip()
    selected_provider = provider.strip()

    preset = _PRESET_BY_LABEL.get(preset_label)
    if preset is not None:
        selected_model = preset.model_id
        selected_provider = preset.provider

    if not selected_provider:
        selected_provider = infer_provider(selected_model)

    return selected_model, selected_provider
