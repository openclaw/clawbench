from pathlib import Path

import pytest

from clawbench.schemas import BackgroundService
from clawbench.services import build_runtime_values, start_background_services, stop_background_services


@pytest.mark.asyncio
async def test_background_service_waits_for_ready_file(tmp_path: Path):
    script = tmp_path / "service.py"
    script.write_text(
        "from pathlib import Path\n"
        "import time\n"
        "Path('ready.txt').write_text('ok', encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    runtime_values = build_runtime_values(workspace=tmp_path, repo_root=Path.cwd())
    service = BackgroundService(
        name="ready_file_service",
        command="{python_exe} service.py",
        ready_file="ready.txt",
        startup_timeout_seconds=5,
    )

    services, _ = await start_background_services(
        [service],
        workspace=tmp_path,
        repo_root=Path.cwd(),
        runtime_values=runtime_values,
    )
    try:
        assert (tmp_path / "ready.txt").exists()
    finally:
        await stop_background_services(services)


@pytest.mark.asyncio
async def test_background_service_rejects_cwd_outside_workspace(tmp_path: Path):
    runtime_values = build_runtime_values(workspace=tmp_path, repo_root=Path.cwd())
    service = BackgroundService(
        name="bad_service",
        command="true",
        cwd="..",
        ready_path=None,
    )

    with pytest.raises(ValueError, match="escapes workspace"):
        await start_background_services(
            [service],
            workspace=tmp_path,
            repo_root=Path.cwd(),
            runtime_values=runtime_values,
        )
