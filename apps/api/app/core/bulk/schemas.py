"""Request and response shapes for the bulk edit / delete surface.

Names are prefixed ``Bulk…`` rather than left generic on purpose: FastAPI qualifies a component
in **both** modules the moment two of them share a schema name, so a bare ``Result`` here would
quietly rename someone else's in the generated client.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

#: The most rows one bulk call may touch.
#:
#: The pager offers 25 / 50 / 100 / 200 per page and selection is **per page** by construction
#: (``DataTable`` clears it whenever the row set changes), so this is exactly the largest
#: selection the screen can hand over — not a ration, a bound on the per-row work the batch
#: fans out into.
MAX_BULK_IDS = 200


class BulkIds(BaseModel):
    """The selection every bulk action carries.

    Duplicates are collapsed by the service, so a row that arrived twice is written once.
    """

    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=MAX_BULK_IDS)


class BulkUpdateRequest(BulkIds):
    """A selection plus the fields to set on all of it.

    ``values`` is keyed by the entity's own column keys (the same stable keys its CSV export
    uses). **An absent key leaves every row's own value alone; an explicit ``null`` clears it**
    where the column allows clearing at all — the dialog opens blank over rows that disagree
    with each other, so "I did not fill this in" must never mean "empty it everywhere".
    """

    values: dict[str, str | None] = Field(..., min_length=1)


class BulkDeleteRequest(BulkIds):
    """A selection to delete. Permanent, and per row: the rows the batch could do are done."""


class BulkActionFailure(BaseModel):
    """One row the batch could not do, and why.

    ``error`` is an i18n key from the same vocabulary the single-row endpoints raise — and where
    that endpoint would have answered 422 with the reason under a *field*, this carries the field's
    key rather than the envelope's (see ``BulkService._reason``). "3 rows failed: invalid" is not
    a reason, and it is what most refusals would otherwise read as.
    """

    id: uuid.UUID
    error: str


class BulkActionResult(BaseModel):
    """What a bulk call actually did.

    Rows are independent: an ineligible or stale one is **reported, never raised**. Raising
    mid-batch would roll the whole request back (``require_context`` rolls back on any
    exception), so one row a colleague had already changed in another tab would silently undo
    the forty-nine that worked. Each row therefore runs inside its own SAVEPOINT — the failure
    of one leaves the transaction usable for the next.

    A payload-level problem — a status that is not a status, a client that does not exist — is
    still a 422 for the whole call, because it is the caller's and every row would fail on it
    identically.
    """

    succeeded: int
    failed: list[BulkActionFailure] = Field(default_factory=list)
