"""Money helpers.

The space Billing and Balance APIs return every amount twice: in microdollars (millionths of
a US dollar, an integer) and in dollars (a float). The Logs APIs return ``charge`` in dollars
as a float. Floats drift when summed, so everything here is converted to integer microdollars
first and formatted only for display.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

MICRO = 1_000_000


def dollars_to_micro(value: object) -> int | None:
    """Convert a dollar amount (float, int or string) to integer microdollars."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    return int((amount * MICRO).to_integral_value(rounding=ROUND_HALF_EVEN))


def format_micro(micro: int | None, places: int = 2) -> str:
    """Format microdollars as US dollars, for example ``$1,234.50`` or ``-$0.0085``."""
    if micro is None:
        return "—"
    quantum = Decimal(1).scaleb(-places)
    amount = (Decimal(micro) / MICRO).quantize(quantum, rounding=ROUND_HALF_EVEN)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.{places}f}"


def share(part: int, whole: int) -> float:
    """``part`` as a percentage of ``whole``, clamped to 0-100, for bar widths."""
    if whole <= 0 or part <= 0:
        return 0.0
    return min(100.0, round(part * 100 / whole, 2))
