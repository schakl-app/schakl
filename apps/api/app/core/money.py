"""One way to print an amount of money as text — the document's way.

Lifted out of the invoice renderer (``invoicing/render/context.py``, which still imports it
under the same name) the day a second module needed to print a fee *onto* an invoice: a
subscription's note fills ``{{amount}}`` in beside the totals the renderer draws, and a note
reading ``€25.00`` under a total reading ``€ 25,00`` is one document disagreeing with itself.
The web's ``fmtMoney`` is ``Intl``'s answer for the same locales; this is the printed twin.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def fmt_money(value: Any, currency: str, locale: str) -> str:
    """``€ 1.250,00`` in Dutch and German, ``€ 1,250.00`` elsewhere; the code where no symbol
    is known (``CHF 12.50``)."""
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    whole, frac = divmod(abs(amount), 1)
    digits = f"{int(whole):,}"
    cents = f"{int(round(frac * 100)):02d}"
    if locale.startswith("nl") or locale.startswith("de"):
        digits = digits.replace(",", ".")
        formatted = f"{digits},{cents}"
    else:
        formatted = f"{digits}.{cents}"
    sign = "-" if amount < 0 else ""
    symbol = CURRENCY_SYMBOLS.get(currency)
    return f"{sign}{symbol} {formatted}" if symbol else f"{sign}{currency} {formatted}"
