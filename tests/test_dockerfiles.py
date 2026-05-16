from pathlib import Path


def test_public_dockerfiles_copy_public_task_sets():
    repo_root = Path(__file__).resolve().parent.parent

    for dockerfile_name in ("Dockerfile", "Dockerfile.main", "Dockerfile.clawbench-426-agent-hotfix"):
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
