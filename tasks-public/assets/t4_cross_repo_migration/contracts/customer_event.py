def validate_event(payload: dict[str, object]) -> dict[str, object]:
    if "customer_name" not in payload:
        raise ValueError("missing customer_name")
    return {"customer_name": payload["customer_name"], "status": payload["status"]}

