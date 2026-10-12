"""Formatting filters for the UI (French amounts)."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django import template
from django.utils import timezone

register = template.Library()

NARROW_NBSP = " "
NBSP = " "


@register.filter
def eur(value, currency="€"):
    """Format an amount the French way: ``220 052,70 €``. Empty values give ``—``."""
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return "—"
    sign = "-" if amount < 0 else ""
    integer, _, cents = f"{abs(amount):.2f}".partition(".")
    groups = []
    while integer:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    formatted = f"{sign}{NARROW_NBSP.join(groups)},{cents}"
    return f"{formatted}{NBSP}{currency}" if currency else formatted


@register.filter
def percent(value):
    """Format a rate the French way: ``20 %``, ``5,5 %``."""
    if value is None or value == "":
        return "—"
    try:
        rate = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError):
        return "—"
    text = f"{rate:f}".replace(".", ",")
    return f"{text}{NBSP}%"


@register.filter
def initials(user):
    """Two-letter monogram for the user avatar."""
    name = (user.get_full_name() or user.email or user.get_username() or "?").strip()
    parts = [p for p in name.replace("@", " ").replace(".", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


@register.filter
def isodatetime(value):
    """Render an ISO 8601 string from the API as ``12/03/2026 à 13:10``."""
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return value
    if timezone.is_aware(parsed):
        parsed = timezone.localtime(parsed)
    return parsed.strftime("%d/%m/%Y à %H:%M")
