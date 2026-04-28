from pricing import apply_discount


def checkout_total(subtotal: int, discount_percent: int) -> int:
    return apply_discount(subtotal, discount_percent)

