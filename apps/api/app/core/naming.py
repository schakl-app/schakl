"""Which of a client's two names a surface is asking for.

A client has a **label** — what the agency calls them, what every list, picker, panel, report,
notification and Drive folder prints — and, optionally, a **legal name**: the entity a document
must be addressed to. ``companies.legal_name`` is ``NULL`` for most clients, and ``NULL`` means
*the label is also the legal name* rather than *nobody filled this in*.

So there is exactly one rule — ``legal_name or name`` — and it lives here rather than in either
module that needs it. Invoicing reads it (through raw SQL rows, hence the mapping form), the
accounting integrations read it, and the companies module publishes it on the model; writing
``company.legal_name or company.name`` in eight places is how the ninth place comes to forget
the ``or``, print an empty bill-to on the clients that *do* have a legal name, and be discovered
by a customer rather than by us.

Whitespace is stripped and a blank string is treated as absent, because a form posts ``""`` for
an empty box and the service normalises that to ``NULL`` — but an import, a bulk write or an
older row may still carry one, and a bill-to headed by a space is not a state worth having.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["document_name", "document_name_of"]


def document_name(name: str | None, legal_name: str | None) -> str:
    """The name a *document* is addressed to: the legal name where there is one, else the label."""
    return (legal_name or "").strip() or (name or "").strip()


def document_name_of(row: Any) -> str:
    """``document_name`` for a whole row — an ORM instance, a SQLAlchemy ``Row``, or a mapping.

    Invoicing reads companies through hand-written SQL (it does not own the table and does not
    import the module's models), so the snapshot path hands this a mapping; SnelStart hands it a
    ``Company``. One function so the two cannot resolve the pair differently.
    """
    if isinstance(row, Mapping):
        return document_name(row.get("name"), row.get("legal_name"))
    return document_name(getattr(row, "name", None), getattr(row, "legal_name", None))
