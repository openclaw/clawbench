from pathlib import Path


def test_ci_uses_blacksmith_for_openclaw_with_fork_fallback():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "blacksmith-8vcpu-ubuntu-2404" in workflow
    assert "ubuntu-latest" in workflow
    assert "github.repository_owner == 'openclaw'" in workflow


def test_testbox_workflow_hydrates_secrets_and_dotfiles():
    workflow = Path(".github/workflows/ci-check-testbox.yml").read_text(encoding="utf-8")

    assert "useblacksmith/begin-testbox@v2" in workflow
    assert "useblacksmith/run-testbox@v2" in workflow
    assert "scripts/ci-hydrate-testbox-env.sh" in workflow
    assert "HF_TOKEN" in workflow
    assert "OPENCLAW_CODEX_AUTH_JSON" in workflow
    assert "CLAWBENCH_CODEX_AUTH_JSON" in workflow


def test_testbox_helper_sources_hydrated_profile():
    script = Path("scripts/ci-hydrate-testbox-env.sh").read_text(encoding="utf-8")

    assert ".clawbench-testbox-live.profile" in script
    assert "clawbench-testbox-env" in script
    assert "source \"$profile_path\"" in script


def test_hf_sync_ensures_space_before_push():
    workflow = Path(".github/workflows/sync-to-hf-space.yml").read_text(encoding="utf-8")

    assert "Ensure HF Space exists" in workflow
    assert "api.create_repo(" in workflow
    assert "space_sdk=\"docker\"" in workflow
    assert "steps.hf.outputs.username" in workflow
