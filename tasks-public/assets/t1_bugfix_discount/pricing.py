def apply_discount(subtotal_cents: int, discount_percent: int) -> int:
    # BUG: this subtracts the raw percent value instead of a percentage of the subtotal.
    return subtotal_cents - discount_percent

