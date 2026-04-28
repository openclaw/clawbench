from cart import checkout_total


def test_percentage_discount_applies_to_full_subtotal():
    assert checkout_total(2_000, 10) == 1_800


def test_zero_discount_keeps_subtotal():
    assert checkout_total(1_250, 0) == 1_250

