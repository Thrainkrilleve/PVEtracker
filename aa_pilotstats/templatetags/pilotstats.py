"""Template helpers: {% load pilotstats %}."""

# Standard Library
from decimal import Decimal, InvalidOperation

# Django
from django import template

register = template.Library()


@register.filter
def isk(value):
    """1234567890 → "1.23B ISK"; 0 or empty → "0 ISK"."""
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return value
    for size, suffix in ((Decimal(1e12), "T"), (Decimal(1e9), "B"), (Decimal(1e6), "M"), (Decimal(1e3), "K")):
        if abs(amount) >= size:
            return f"{amount / size:.2f}{suffix} ISK"
    return f"{amount:.0f} ISK"


@register.filter
def type_name(type_id, names):
    """Cached type name from a ``{type_id: name}`` dict, else "Type <id>"."""
    if not type_id:
        return ""
    return (names or {}).get(int(type_id)) or f"Type {type_id}"


@register.filter
def get_item(mapping, key):
    return (mapping or {}).get(key)
