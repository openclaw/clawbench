def render_account(event: dict[str, object]) -> str:
    return f"{event['customer_name']} ({event['status']})"

