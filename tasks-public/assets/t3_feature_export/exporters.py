import json


def export_json(issues: list[dict[str, object]]) -> str:
    return json.dumps(issues, sort_keys=True)


def export_csv(issues: list[dict[str, object]]) -> str:
    raise NotImplementedError("csv export is not implemented yet")

