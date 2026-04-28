from __future__ import annotations

import csv
import json
import sys


def load_sales(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_regions(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def build_report(sales_rows: list[dict[str, str]], region_map: dict[str, str]) -> str:
    # TODO: aggregate all rows by region and include totals.
    first = sales_rows[0]
    region_name = region_map[first["region"]]
    return f"{region_name}: {first['amount']}"


if __name__ == "__main__":
    sales = load_sales(sys.argv[1])
    regions = load_regions(sys.argv[2])
    print(build_report(sales, regions))

