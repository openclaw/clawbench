from pathlib import Path


def test_public_dockerfiles_copy_public_task_sets():
    repo_root = Path(__file__).resolve().parent.parent

    for dockerfile_name in ("Dockerfile", "Dockerfile.main"):
        dockerfile = repo_root / dockerfile_name
        contents = dockerfile.read_text(encoding="utf-8")

        assert (
            "COPY --chown=node:node pyproject.toml README.md CLAWBENCH_V0_4_SPEC.md "
            "PARTNER_TRACE_SPEC.md ./"
        ) in contents
        assert "COPY --chown=node:node tasks-public/ tasks-public/" in contents
        assert "COPY --chown=node:node profiles/ profiles/" in contents
        assert "COPY --chown=node:node baselines/ baselines/" in contents
        assert "COPY --chown=node:node tasks-domain/ tasks-domain/" in contents
        assert "COPY --chown=node:node tasks/ tasks/" not in contents


def test_container_eval_scripts_do_not_write_codex_plugin_config():
    repo_root = Path(__file__).resolve().parent.parent

    for script_name in ("scripts/container_adapter_eval.sh", "scripts/container_lane_eval.sh"):
        contents = (repo_root / script_name).read_text(encoding="utf-8")

        assert "codex.setdefault(\"config\"" not in contents
        assert "config[\"codexDynamicToolsLoading\"]" not in contents
        assert "model_cfg[\"agentRuntime\"]" not in contents
        assert "openai-codex:clawbench-env" in contents
        assert "PI_CODING_AGENT_DIR" in contents
