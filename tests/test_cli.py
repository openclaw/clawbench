from clawbench.cli import SCENARIO_CHOICES
from clawbench.schemas import ScenarioDomain


def test_cli_scenario_choices_track_schema_enum():
    assert SCENARIO_CHOICES == [scenario.value for scenario in ScenarioDomain]
