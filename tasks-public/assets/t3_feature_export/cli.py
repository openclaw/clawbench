from __future__ import annotations

import argparse

from exporters import export_csv, export_json
from issues import ISSUES


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["export"])
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    args = parser.parse_args()

    if args.format == "json":
        print(export_json(ISSUES))
        return

    print(export_csv(ISSUES))


if __name__ == "__main__":
    main()
