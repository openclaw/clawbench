import tomllib
from pathlib import Path


def test_wheel_includes_runtime_data_directories():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert force_include["tasks-public"] == "tasks-public"
    assert force_include["profiles"] == "profiles"
    assert force_include["baselines"] == "baselines"
