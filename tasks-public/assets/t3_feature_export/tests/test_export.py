from exporters import export_csv
from issues import ISSUES


def test_csv_export_has_header_and_rows():
    assert export_csv(ISSUES) == (
        "id,title,status\n"
        "101,Fix login loop,open\n"
        "102,Improve metrics panel,closed\n"
    )

