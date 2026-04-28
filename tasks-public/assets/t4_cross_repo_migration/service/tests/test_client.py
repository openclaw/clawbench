from service.render import render_account


def test_service_uses_account_name():
    assert render_account({"account_name": "Acme", "status": "active"}) == "Acme (active)"

