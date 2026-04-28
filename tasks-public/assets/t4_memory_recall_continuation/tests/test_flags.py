from flags import BETA_REGIONS, RETRY_BUDGET, APAC_GATED_UNTIL


def test_beta_regions():
    assert BETA_REGIONS == ["us", "eu"]


def test_retry_budget():
    assert RETRY_BUDGET == 3


def test_apac_gated_until():
    # APAC gating lifts at release 2026.3 per the rollout plan.
    assert APAC_GATED_UNTIL == "2026.3"
