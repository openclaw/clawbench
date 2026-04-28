from contracts.customer_event import validate_event


def test_schema_uses_account_name():
    payload = validate_event({"account_name": "Acme", "status": "active"})
    assert payload["account_name"] == "Acme"

