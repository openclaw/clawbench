from billing import monthly_total
from notifications import subject_for


def test_monthly_total_applies_percentage_fee():
    assert monthly_total(10_000, 5) == 10_500


def test_subject_title_cases_name_and_uppercases_status():
    assert subject_for("acme west", "warning") == "[WARNING] Acme West"

