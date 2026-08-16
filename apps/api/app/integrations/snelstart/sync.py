"""Moving records between schakl and SnelStart (epic #377). Business-licensed — see LICENSE.

The four syncs, and the one principle behind all of them: **schakl is the CRM and SnelStart is
the ledger** (#31). So relations, articles and invoices flow *out*, and only one thing flows
back — whether the money arrived, which is the question SnelStart is authoritative about and the
reason an agency wanted this integration at all.

Everything here obeys three rules that a finance sync earns the hard way.

**Never create the same invoice twice.** The idempotency is structural rather than careful:
before a push, the stored link says what we did last time; when the link is missing, the
administration is *asked* by invoice number; and when SnelStart answers ``BOE-0021`` (*"het
factuurnummer bestaat al"*) that is not a failure — it is SnelStart telling us the document is
already there, and the correct response is to go and adopt it. A timeout is neither success nor
failure but **unknown**, and an unknown write is followed by a lookup, never by a retry.

**A row's failure is that row's.** A batch runs each record in its own savepoint and reports
what it could not do; raising mid-batch would roll back the thirty-nine that worked (§18's
rule, and it matters more here because the thirty-nine are somebody's invoices).

**Matching proposes; a human disposes.** The first connect is the dangerous moment — 200
relations against 180 companies — so a match on the Chamber of Commerce number is applied and a
match on a *name* is only ever offered. Automatically merging two clients who share a word is
not a bug anybody notices until an invoice goes to the wrong company.
"""

from __future__ import annotations

import base64
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.activity import ActivityService
from app.core.naming import document_name_of
from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.errors import AppError
from app.integrations.snelstart.client import (
    CODE_DUPLICATE_INVOICE_NUMBER,
    CODE_RELATIECODE_IN_USE,
    SnelstartClient,
    SnelstartError,
    SnelstartUnknownWriteError,
    odata_string,
    parse_amount,
    parse_moment,
    redact,
)
from app.integrations.snelstart.mapping import (
    MappingError,
    article_code_error,
    article_payload,
    boeking_payload,
    payload_hash,
    relation_payload,
)
from app.integrations.snelstart.models import (
    SnelstartAccount,
    SnelstartLink,
    SnelstartLinkKind,
    SnelstartLinkStatus,
    SnelstartRef,
    SnelstartRefKind,
    SnelstartSyncKind,
    SnelstartSyncRun,
)
from app.integrations.snelstart.schemas import (
    SnelstartPaymentReconcileRow,
    SnelstartPushResult,
    SnelstartRelationCandidate,
)
from app.integrations.snelstart.service import (
    MAX_RUN_ERRORS,
    SnelstartAccountService,
    client_for,
    translate,
)
from app.modules.invoicing.calc import CENTS

logger = logging.getLogger("schakl.snelstart")

#: A relation is matched on these, in this order, and only the first three are ever applied
#: without asking. A Chamber of Commerce number identifies a legal entity; a name identifies
#: nothing — *Jansen bv* and *Jansen Transport bv* are two companies and one substring.
AUTO_MATCH_FIELDS = ("coc", "vat", "client_number")


def is_reviewable(row: Mapping[str, Any]) -> bool:
    """Is this relation a *client* worth pairing, or one of SnelStart's own fixtures?

    Every administration ships with three rows that are not clients: the agency's own relation
    (``Relatiesoort`` contains ``Eigen``, and in a fresh administration it is still called
    *"<Vul hier uw bedrijfsnaam in>"*), and the reserved placeholders ``-1 Leverancier onbekend``
    and ``-2 Klant onbekend``. SnelStart reserves negative relatiecodes for exactly this.

    Left in, they are two thirds of a first review's "needs a decision" list — noise in the one
    place an admin has to read every row, and the list is only useful if every row on it is a
    real question.
    """
    if "Eigen" in (row.get("relatiesoort") or []):
        return False
    code = row.get("relatiecode")
    return not (isinstance(code, int) and code < 0)


class SnelstartSyncService:
    """The four syncs. One service, because they share a client, a run log and a link table."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.accounts = SnelstartAccountService(ctx)
        self.links = ctx.repo(SnelstartLink)
        self.refs = ctx.repo(SnelstartRef)
        self.activity = ActivityService(ctx)

    # ------------------------------------------------------------------ #
    # Relations
    # ------------------------------------------------------------------ #
    async def relation_candidates(
        self, account_id: uuid.UUID
    ) -> list[SnelstartRelationCandidate]:
        """Every SnelStart customer, and what schakl believes it is.

        The review screen for a first connect. Nothing is written: this reads the administration
        and the CRM, proposes pairings, and shows *why* each one is proposed — because an admin
        scanning 200 rows needs to know which are facts (a KvK match) and which are guesses (a
        name match) so they only have to actually read the guesses.
        """
        account = await self.accounts.accounts.get_or_404(account_id)
        client = client_for(account)
        async with self.ctx.release_db():
            rows = await client.fetch_all(
                "relaties",
                filter_="Relatiesoort/any(r:r eq 'Klant')",
                match=lambda row: "Klant" in (row.get("relatiesoort") or [])
                and is_reviewable(row),
            )
        linked = {
            link.external_id: link
            for link in await self._links_of(account.id, SnelstartLinkKind.RELATION)
        }
        index = await self._company_index()
        out: list[SnelstartRelationCandidate] = []
        for row in rows:
            external_id = str(row.get("id") or "")
            if not external_id:
                continue
            link = linked.get(external_id)
            company_id, match_on = (
                (link.local_id, "linked") if link and link.local_id else _match_company(row, index)
            )
            out.append(
                SnelstartRelationCandidate(
                    external_id=external_id,
                    external_code=_str(row.get("relatiecode")),
                    name=str(row.get("naam") or ""),
                    email=_str(row.get("email")),
                    vat_number=_str(row.get("btwNummer")),
                    coc_number=_str(row.get("kvkNummer")),
                    company_id=company_id,
                    company_name=index.names.get(company_id) if company_id else None,
                    match_on=match_on,
                    linked=bool(link and link.local_id),
                    # What `adopt` is addressed by. Absent until `sync/relations` has created
                    # the link row, which is the ordering the review screen states out loud.
                    link_id=link.id if link is not None else None,
                )
            )
        out.sort(key=lambda c: (c.linked, c.company_id is None, c.name.lower()))
        return out

    async def link_relations(self, account_id: uuid.UUID) -> SnelstartSyncRun:
        """Adopt every SnelStart customer schakl can identify **without guessing**.

        Only :data:`AUTO_MATCH_FIELDS` — the identifiers that identify. A name match is left for
        the review screen, and a relation nothing matches is stored as ``unlinked``, which is a
        real state worth keeping: it is how an agency sees that their bookkeeper has 40 clients
        the CRM has never heard of.
        """
        account = await self.accounts.accounts.get_or_404(account_id)
        run = await self.accounts._start_run(account, SnelstartSyncKind.RELATIONS)
        client = client_for(account)
        try:
            async with self.ctx.release_db():
                rows = await client.fetch_all(
                    "relaties",
                    filter_="Relatiesoort/any(r:r eq 'Klant')",
                    match=lambda row: "Klant" in (row.get("relatiesoort") or [])
                    and is_reviewable(row),
                )
        except SnelstartError as exc:
            return await self.accounts._fail_run(
                run, account, redact(str(exc))[:500], exc=exc
            )

        index = await self._company_index()
        existing = {
            link.external_id: link
            for link in await self._links_of(account.id, SnelstartLinkKind.RELATION)
        }
        counts = {"read": len(rows), "linked": 0, "unlinked": 0, "already": 0}
        now = datetime.now(UTC)
        for row in rows:
            external_id = str(row.get("id") or "")
            if not external_id:
                continue
            link = existing.get(external_id)
            if link is not None and link.local_id:
                counts["already"] += 1
                await self._observe(link, row, now)
                continue
            company_id, match_on = _match_company(row, index)
            if match_on not in AUTO_MATCH_FIELDS:
                company_id = None
            if link is None:
                link = await self.links.create(
                    account_id=account.id,
                    kind=SnelstartLinkKind.RELATION.value,
                    external_id=external_id,
                    local_type="company" if company_id else None,
                    local_id=company_id,
                    company_id=company_id,
                    status=(
                        SnelstartLinkStatus.ACTIVE.value
                        if company_id
                        else SnelstartLinkStatus.UNLINKED.value
                    ),
                )
            else:
                link.local_id = company_id
                link.local_type = "company" if company_id else None
                link.company_id = company_id
                link.status = (
                    SnelstartLinkStatus.ACTIVE.value
                    if company_id
                    else SnelstartLinkStatus.UNLINKED.value
                )
            await self._observe(link, row, now)
            counts["linked" if company_id else "unlinked"] += 1

        account.last_synced_at = now
        return await self.accounts._finish_run(run, ok=True, counts=counts)

    async def push_relation(
        self, account: SnelstartAccount, company: Any, *, client: SnelstartClient | None = None
    ) -> SnelstartPushResult:
        """One schakl company into SnelStart, creating or updating as needed.

        Reads the relation back before writing it, always. That is not a wasted call: the
        payload is **merged onto what is there** (a PUT replaces the whole record and would
        otherwise blank the bookkeeper's memo and credit limit), and reading is also how a push
        notices the record was deleted in SnelStart since we last looked.
        """
        client = client or client_for(account)
        link = await self._link_for(account.id, SnelstartLinkKind.RELATION, company.id)
        country = await self.accounts.country_id(account, getattr(company, "country", None))
        contact_name = await self._primary_contact_name(company.id)

        existing: dict[str, Any] | None = None
        if link is not None:
            async with self.ctx.release_db():
                existing = await client.get("relaties", link.external_id)
            if existing is None:
                # The relation we created is gone. Not an error and not a reason to stop: the
                # bookkeeper deleted it, and the right answer is to make a new one and say so.
                link.status = SnelstartLinkStatus.MISSING.value

        try:
            payload = relation_payload(
                company,
                country_id=country,
                contact_name=contact_name,
                existing=existing,
            )
            # The digest is taken over **schakl's own contribution**, not the merged payload.
            # A merged payload legitimately differs between a create and an update — it carries
            # the bookkeeper's memo, credit limit and mandate the second time — so hashing it
            # would make every nightly sync see a change and rewrite five hundred unchanged
            # relations. What we are asking is "has *our* answer changed", and this is it.
            digest = payload_hash(
                relation_payload(
                    company, country_id=country, contact_name=contact_name, existing=None
                )
            )
        except MappingError as exc:
            return await self._link_failed(link, exc.message_key, detail=exc.detail)
        if link is not None and existing is not None and link.push_hash == digest:
            # Nothing to say. On a nightly sync this is the answer for almost every row, and
            # skipping the write is the difference between 500 round-trips and none.
            return SnelstartPushResult(
                ok=True,
                external_id=link.external_id,
                external_code=link.external_code,
                action="unchanged",
            )

        try:
            async with self.ctx.release_db():
                if existing is not None and link is not None:
                    await client.put("relaties", link.external_id, payload)
                    result = await client.get("relaties", link.external_id) or payload
                    action = "updated"
                else:
                    result = await client.post("relaties", payload) or {}
                    action = "created"
        except SnelstartError as exc:
            if exc.code == CODE_RELATIECODE_IN_USE and existing is None:
                # The client number this company carries is already somebody's relatiecode.
                # That is a collision between two independent numbering systems and it must not
                # cost the agency a client record — the code is a convenience, the relation is
                # the requirement. So we look at who holds it (it is very often this same
                # client, entered by the bookkeeper first), and either adopt them or create
                # without a code and let SnelStart allocate its own.
                resolved = await self._resolve_relatiecode_clash(
                    account, company, payload, client=client
                )
                if resolved is not None:
                    return resolved
            return await self._link_failed(
                link, translate(exc).message_key, detail=redact(str(exc))[:500]
            )

        external_id = str(result.get("id") or (link.external_id if link else ""))
        if not external_id:
            return await self._link_failed(link, "errors.snelstart.request_failed")

        now = datetime.now(UTC)
        if link is None:
            link = await self.links.create(
                account_id=account.id,
                kind=SnelstartLinkKind.RELATION.value,
                external_id=external_id,
                local_type="company",
                local_id=company.id,
                company_id=company.id,
                status=SnelstartLinkStatus.ACTIVE.value,
            )
        link.external_id = external_id
        link.push_hash = digest
        link.pushed_at = now
        link.status = SnelstartLinkStatus.ACTIVE.value
        link.last_error = None
        await self._observe(link, result, now)
        return SnelstartPushResult(
            ok=True,
            external_id=external_id,
            external_code=link.external_code,
            action=action,
        )

    async def _resolve_relatiecode_clash(
        self,
        account: SnelstartAccount,
        company: Any,
        payload: dict[str, Any],
        *,
        client: SnelstartClient,
    ) -> SnelstartPushResult | None:
        """Somebody already holds this relatiecode. Work out whether it is this client.

        Two numbering systems that were never coordinated will collide, and the failure this
        replaces was the worst possible shape: a client simply not reaching the books, reported
        as "SnelStart weigert dit verzoek" with nothing an admin could do about it except
        renumber their CRM.

        **Adopt rather than duplicate, and never guess.** The relation holding the code is
        fetched and compared on the identifiers that identify — Chamber of Commerce number,
        then VAT number, then an exact name. A match is adopted (the bookkeeper entered this
        client before we did, which is the normal case). No match means the number simply
        belongs to somebody else, so the relation is created **without** it and SnelStart
        allocates its own; the link then records the code it really got, so the screen tells the
        truth rather than what we asked for.
        """
        code = payload.get("relatiecode")
        if code is None:
            return None
        async with self.ctx.release_db():
            holders = await client.fetch(
                "relaties",
                match=lambda row: row.get("relatiecode") == code,
            )
        holder = holders[0] if holders else None

        if holder is not None and _same_company(holder, company):
            link = await self.links.create(
                account_id=account.id,
                kind=SnelstartLinkKind.RELATION.value,
                external_id=str(holder.get("id") or ""),
                local_type="company",
                local_id=company.id,
                company_id=company.id,
                status=SnelstartLinkStatus.ACTIVE.value,
            )
            # No ``push_hash``: we did not send this, so the next push writes once and only
            # then may the hash mean what it says.
            await self._observe(link, holder, datetime.now(UTC))
            return SnelstartPushResult(
                ok=True,
                external_id=link.external_id,
                external_code=link.external_code,
                action="adopted",
            )

        retry = {key: value for key, value in payload.items() if key != "relatiecode"}
        try:
            async with self.ctx.release_db():
                result = await client.post("relaties", retry) or {}
        except SnelstartError as exc:
            return await self._link_failed(
                None, translate(exc).message_key, detail=redact(str(exc))[:500]
            )
        external_id = str(result.get("id") or "")
        if not external_id:
            return None
        link = await self.links.create(
            account_id=account.id,
            kind=SnelstartLinkKind.RELATION.value,
            external_id=external_id,
            local_type="company",
            local_id=company.id,
            company_id=company.id,
            status=SnelstartLinkStatus.ACTIVE.value,
            push_hash=payload_hash(retry),
            pushed_at=datetime.now(UTC),
        )
        await self._observe(link, result, datetime.now(UTC))
        return SnelstartPushResult(
            ok=True,
            external_id=external_id,
            external_code=link.external_code,
            action="created",
        )

    async def push_relations(self, account_id: uuid.UUID) -> SnelstartSyncRun:
        """Every company that is already paired, or that has an invoice worth pairing for.

        Deliberately **not** every company in the CRM. An agency's companies table holds
        prospects, former clients and the one-off they quoted in 2019; pushing all of them into
        somebody's bookkeeping is filling a ledger with records nobody asked for. What earns a
        relation is being invoiced.
        """
        account = await self.accounts.accounts.get_or_404(account_id)
        run = await self.accounts._start_run(account, SnelstartSyncKind.RELATIONS)
        companies = await self._companies_worth_pushing(account)
        client = client_for(account)
        counts = {"read": len(companies), "created": 0, "updated": 0, "unchanged": 0, "failed": 0}
        errors: list[dict[str, Any]] = []

        for company in companies:
            result = await self._row(self.push_relation(account, company, client=client))
            if result.ok:
                key = result.action or "unchanged"
                counts[key] = counts.get(key, 0) + 1
            else:
                counts["failed"] += 1
                if len(errors) < MAX_RUN_ERRORS:
                    errors.append(
                        {
                            "local_id": str(company.id),
                            "name": company.name,
                            "key": result.error_key,
                            "message": result.error,
                        }
                    )

        account.last_synced_at = datetime.now(UTC)
        return await self.accounts._finish_run(
            run, ok=counts["failed"] == 0, counts=counts, errors=errors
        )

    # ------------------------------------------------------------------ #
    # Invoices
    # ------------------------------------------------------------------ #
    async def push_invoice(
        self,
        account: SnelstartAccount,
        invoice: Any,
        *,
        client: SnelstartClient | None = None,
    ) -> SnelstartPushResult:
        """One issued invoice into SnelStart's sales ledger.

        The idempotency is four-layered and each layer covers a failure the previous one cannot:

        1. **The stored link.** We already pushed it; update rather than create.
        2. **A lookup by number.** No link, but the number may still be there — from a previous
           install, from a bookkeeper typing it in, or from a push whose answer we never saw.
        3. **``BOE-0021``.** SnelStart refusing a duplicate number is not a failure; it is the
           answer to *"is it already there?"*, and we go and adopt it.
        4. **An unknown write.** A timeout or a 502 means the boeking may exist. We look, and
           only then decide — the one thing #31 says never to guess about.
        """
        from app.modules.invoicing.models import InvoiceStatus

        client = client or client_for(account)
        if invoice.status == InvoiceStatus.DRAFT.value or not invoice.number:
            raise AppError(
                "snelstart_invoice_not_issued",
                "errors.snelstart.invoice_not_issued",
                status_code=409,
            )

        # Lines must be on the invoice before anything else: the planner needs them for the
        # boeking and the renderer needs them for the PDF. A row loaded straight off
        # ``scoped_select`` has neither, and the renderer's failure is *swallowed* by design —
        # so forgetting this would silently ship boekingen with no document attached.
        invoice = await self._with_lines(invoice)
        relation_id = await self._relation_id_for(account, invoice, client=client)
        link = await self._link_for(account.id, SnelstartLinkKind.INVOICE, invoice.id)

        if link is None:
            # (2) The number may already be booked. Asked before writing, because
            # ``verkoopboekingen`` has no read-by-number and ``verkoopfacturen`` does.
            found = await self._find_boeking_by_number(client, invoice.number)
            if found is not None:
                # **Adopt and stop.** A boeking under this number that we did not write was
                # written by somebody — a previous install, or a bookkeeper typing it in — and
                # overwriting it on sight is the silent-overwrite failure every mirroring
                # integration here is warned against. It is recorded as ours, marked as drift
                # when it differs from what we would have sent, and left to a human.
                link = await self._adopt_invoice(account, invoice, found)
                return SnelstartPushResult(
                    ok=True,
                    external_id=link.external_id,
                    external_code=invoice.number,
                    # Adopted **and** disagreeing is its own outcome, not a footnote on the
                    # panel: the sync log is where an admin looks, and "1 overgenomen" with no
                    # mention that its amount is different is the silent half of a silent
                    # overwrite.
                    action=(
                        "drift"
                        if link.status == SnelstartLinkStatus.DRIFT.value
                        else "adopted"
                    ),
                    error=link.last_error,
                )

        plan = await self._plan_boeking(account, invoice, relation_id, link)
        if isinstance(plan, SnelstartPushResult):
            return plan
        payload, guessed = plan
        digest = payload_hash(payload)
        if link is not None and link.push_hash == digest and link.status == (
            SnelstartLinkStatus.ACTIVE.value
        ):
            return SnelstartPushResult(
                ok=True,
                external_id=link.external_id,
                external_code=invoice.number,
                action="unchanged",
                guessed_rates=guessed,
            )

        action = "updated" if link is not None else "created"
        try:
            async with self.ctx.release_db():
                if link is not None:
                    payload = {**payload, "id": link.external_id}
                    await client.put("verkoopboekingen", link.external_id, payload)
                    external_id = link.external_id
                else:
                    result = await client.post("verkoopboekingen", payload) or {}
                    external_id = str(result.get("id") or "")
        except SnelstartUnknownWriteError as exc:
            # (4) Nothing answered. The boeking may be there; a blind retry would be a second
            # invoice in somebody's ledger, which is the incident #31 exists to prevent.
            found = await self._find_boeking_by_number(client, invoice.number)
            if found is None:
                return await self._link_failed(
                    link, "errors.snelstart.push_unknown", detail=redact(str(exc))[:500]
                )
            link = await self._adopt_invoice(account, invoice, found)
            external_id, action = link.external_id, "adopted"
        except SnelstartError as exc:
            if exc.code == CODE_DUPLICATE_INVOICE_NUMBER:
                # (3) Not a failure — SnelStart saying it is already there.
                found = await self._find_boeking_by_number(client, invoice.number)
                if found is not None:
                    link = await self._adopt_invoice(account, invoice, found)
                    return SnelstartPushResult(
                        ok=True,
                        external_id=link.external_id,
                        external_code=invoice.number,
                        action=(
                            "drift"
                            if link.status == SnelstartLinkStatus.DRIFT.value
                            else "adopted"
                        ),
                        error=link.last_error,
                        guessed_rates=guessed,
                    )
            return await self._link_failed(
                link, translate(exc).message_key, detail=redact(str(exc))[:500]
            )

        if not external_id:
            return await self._link_failed(link, "errors.snelstart.request_failed")

        now = datetime.now(UTC)
        if link is None:
            link = await self.links.create(
                account_id=account.id,
                kind=SnelstartLinkKind.INVOICE.value,
                external_id=external_id,
                local_type="invoice",
                local_id=invoice.id,
                company_id=invoice.company_id,
                external_code=invoice.number,
            )
        link.external_id = external_id
        link.external_code = invoice.number
        link.external_name = (invoice.customer or {}).get("name") or None
        link.push_hash = digest
        link.pushed_at = now
        link.last_synced_at = now
        link.status = SnelstartLinkStatus.ACTIVE.value
        link.last_error = None

        if account.attach_invoice_pdf:
            await self._attach_pdf(account, invoice, link, client=client)

        await self._record_external_ref(invoice, link)
        await self.activity.record(
            "invoice",
            invoice.id,
            "snelstart.pushed",
            {"external_id": external_id, "action": action},
        )
        return SnelstartPushResult(
            ok=True,
            external_id=external_id,
            external_code=invoice.number,
            action=action,
            guessed_rates=guessed,
        )

    async def _with_lines(self, invoice: Any) -> Any:
        """The invoice with its lines and derived fields attached, loading them if needed.

        ``InvoiceService._attach`` is what puts ``lines`` (and the tax groups the renderer
        prints) onto an invoice, and a batch that selects rows directly never went through it.
        Idempotent, so pushing one invoice from its own detail page — where it arrives already
        attached — costs no second query.
        """
        if getattr(invoice, "lines", None) is not None:
            return invoice
        from app.modules.invoicing.service import InvoiceService

        return await InvoiceService(self.ctx).get(invoice.id)

    async def _plan_boeking(
        self,
        account: SnelstartAccount,
        invoice: Any,
        relation_id: str,
        link: SnelstartLink | None,
    ) -> tuple[dict[str, Any], list[str]] | SnelstartPushResult:
        """Build the payload, resolving every ledger and rate first.

        Split out so the resolution — which needs the database — happens *before* the write, and
        so a mapping failure is reported as this invoice's problem rather than raised through a
        batch. Returns either the payload or the failure.
        """
        from app.modules.invoicing.models import InvoiceLine, TaxRate
        from app.modules.invoicing.service import _totals_from_rows

        lines = list(getattr(invoice, "lines", None) or [])
        if not lines:
            lines = list(
                (
                    await self.ctx.session.execute(
                        select(InvoiceLine)
                        .where(InvoiceLine.invoice_id == invoice.id)
                        .order_by(InvoiceLine.position, InvoiceLine.id)
                    )
                ).scalars()
            )
        totals = _totals_from_rows(lines, prices_include_tax=invoice.prices_include_tax)

        # Every tax rate the document touches → its stored grootboek number, in one query.
        rate_ids = {line.tax_rate_id for line in lines if line.tax_rate_id}
        ledger_codes: dict[uuid.UUID, str] = {}
        if rate_ids:
            rows = await self.ctx.session.execute(
                self.ctx.repo(TaxRate).scoped_select().where(TaxRate.id.in_(rate_ids))
            )
            ledger_codes = {
                row.id: row.ledger_code for row in rows.scalars() if row.ledger_code
            }
        resolved: dict[str, tuple[str, str]] = {}
        for code in {*ledger_codes.values(), account.default_ledger_code or ""}:
            if code:
                found = await self.accounts.resolve_ledger(account, code)
                if found:
                    resolved[code] = found

        def ledger_for(line: Any) -> tuple[str, str] | None:
            code = ledger_codes.get(line.tax_rate_id) or account.default_ledger_code
            return resolved.get(code or "")

        try:
            plan = boeking_payload(
                invoice,
                lines,
                totals,
                relation_id=relation_id,
                ledger_for=ledger_for,
                vat_rates=await self.accounts.vat_rates(account),
                existing_id=link.external_id if link else None,
            )
        except MappingError as exc:
            return await self._link_failed(link, exc.message_key, detail=exc.detail)
        return plan.payload, plan.guessed_rates

    async def _find_boeking_by_number(
        self, client: SnelstartClient, number: str
    ) -> dict[str, Any] | None:
        """Is this invoice number already booked? Asked through ``verkoopfacturen``.

        ``verkoopboekingen`` has no list operation at all — only get-by-id — while every boeking
        produces a ``verkoopfactuur`` that carries both the number and a pointer back. So the
        lookup goes the long way round, and the predicate is re-applied locally because a filter
        that silently did nothing would make *every* invoice look like a duplicate of the first.
        """
        async with self.ctx.release_db():
            rows = await client.fetch(
                "verkoopfacturen",
                filter_=f"Factuurnummer eq {odata_string(number)}",
                match=lambda row: str(row.get("factuurnummer") or "") == number,
            )
        if not rows:
            return None
        row = rows[0]
        boeking = row.get("verkoopBoeking") or {}
        external_id = str(boeking.get("id") or "")
        return {**row, "_boeking_id": external_id} if external_id else None

    async def _adopt_invoice(
        self, account: SnelstartAccount, invoice: Any, found: dict[str, Any]
    ) -> SnelstartLink:
        """Record a boeking that already existed as ours, and say whether it agrees with us.

        ``push_hash`` is deliberately left ``NULL``: we did not send this payload, so we cannot
        claim it matches. The next push therefore compares against nothing, writes once, and
        from then on the hash means what it says.

        **The amount is compared, and a difference is `drift`.** Adopting is the right answer to
        "somebody already booked this number" — overwriting a bookkeeper's entry is not ours to
        do — but adopting *silently* would leave schakl showing €635,25 and the ledger showing
        €1.428,00 under one number, with nothing anywhere saying they disagree. That is the exact
        state ``drift`` exists to name: it is there, and it is not what we would have written.
        """
        now = datetime.now(UTC)
        link = await self._link_for(account.id, SnelstartLinkKind.INVOICE, invoice.id)
        if link is None:
            link = await self.links.create(
                account_id=account.id,
                kind=SnelstartLinkKind.INVOICE.value,
                external_id=found["_boeking_id"],
                local_type="invoice",
                local_id=invoice.id,
                company_id=invoice.company_id,
            )
        remote_total = parse_amount(found.get("factuurBedrag"))
        agrees = remote_total is not None and remote_total == Decimal(invoice.total)
        link.external_id = found["_boeking_id"]
        link.external_code = invoice.number
        link.status = (
            SnelstartLinkStatus.ACTIVE.value if agrees else SnelstartLinkStatus.DRIFT.value
        )
        link.push_hash = None
        # Not an error — nothing failed — but a fact a human has to resolve, so it is recorded
        # where the screen already reads failures rather than in a log line nobody opens.
        # Quantised to cents on both sides: SnelStart answers ``1428.0`` and schakl holds
        # ``1428.00``, and a message that renders one of them short reads like a rounding
        # difference rather than the eight-hundred-euro one it is actually reporting.
        link.last_error = (
            None
            if agrees
            else (
                f"Boeking in SnelStart: {remote_total.quantize(CENTS)}; "
                f"factuur in schakl: {Decimal(invoice.total).quantize(CENTS)}"
            )[:500]
        )
        link.last_synced_at = now
        link.observed = {k: v for k, v in found.items() if not k.startswith("_")}
        link.observed_at = parse_moment(found.get("modifiedOn")) or now
        await self.ctx.session.flush()
        return link

    async def _attach_pdf(
        self,
        account: SnelstartAccount,
        invoice: Any,
        link: SnelstartLink,
        *,
        client: SnelstartClient,
    ) -> None:
        """Put the rendered invoice on the boeking, once.

        Failure here is recorded and swallowed. The boeking is the thing that had to happen —
        an attachment that did not is a missing convenience, and raising would undo a ledger
        entry that is correct. SnelStart caps an attachment at 10 MB (``BLG-0006``) and we do
        not resend one we already sent, because ``POST /documenten`` has no upsert and would
        quietly stack copies.
        """
        if (link.observed or {}).get("_pdf_document_id"):
            return
        from app.modules.invoicing.service import InvoiceService

        try:
            content, filename = await InvoiceService(self.ctx).document_pdf(invoice, "invoice")
        except Exception as exc:  # noqa: BLE001 — a render failure must not undo the boeking
            logger.warning("snelstart: could not render invoice %s: %s", invoice.id, exc)
            return
        if len(content) > 10 * 1024 * 1024:
            link.last_error = "errors.snelstart.attachment_too_large"
            return
        try:
            async with self.ctx.release_db():
                result = await client.post(
                    "documenten/Verkoopboekingen",
                    {
                        "content": base64.b64encode(content).decode(),
                        "parentIdentifier": link.external_id,
                        "fileName": filename[:120],
                    },
                )
        except SnelstartError as exc:
            link.last_error = redact(str(exc))[:500]
            return
        observed = dict(link.observed or {})
        observed["_pdf_document_id"] = str((result or {}).get("id") or "")
        link.observed = observed

    async def _record_external_ref(self, invoice: Any, link: SnelstartLink) -> None:
        """Also write invoicing's own ``external_refs`` row.

        Two records of one fact, and on purpose: ``snelstart_links`` is this module's working
        state (drift, push hash, the attachment id), while ``invoicing_external_refs`` is the
        provider-independent answer to *"what does a bookkeeping package know about this
        invoice?"* that ``GET /invoices/{id}/refs`` already serves — a route written for #31
        before any provider existed. Skipping it would leave the shipped seam empty.
        """
        from app.modules.invoicing.service import ExternalRefService

        await ExternalRefService(self.ctx).upsert(
            provider="snelstart",
            local_type="invoice",
            local_id=invoice.id,
            external_id=link.external_id,
            payload={
                "factuurnummer": link.external_code,
                "administration": None,
                "pushed_at": link.pushed_at.isoformat() if link.pushed_at else None,
            },
        )

    async def push_invoices(
        self, account_id: uuid.UUID, *, invoice_ids: Sequence[uuid.UUID] | None = None
    ) -> SnelstartSyncRun:
        """Every issued invoice not yet in SnelStart, or a named selection."""
        from app.modules.invoicing.models import Invoice, InvoiceStatus

        account = await self.accounts.accounts.get_or_404(account_id)
        run = await self.accounts._start_run(account, SnelstartSyncKind.INVOICES)

        stmt = (
            self.ctx.repo(Invoice)
            .scoped_select()
            .where(
                Invoice.status.in_(
                    [InvoiceStatus.OPEN.value, InvoiceStatus.PAID.value]
                ),
                Invoice.number.is_not(None),
            )
            .order_by(Invoice.issue_date, Invoice.number)
        )
        if invoice_ids:
            stmt = stmt.where(Invoice.id.in_(list(invoice_ids)))
        else:
            # Everything not already linked and pushed. A LEFT JOIN rather than a per-invoice
            # existence check: an agency's first push is the whole back catalogue.
            done = select(SnelstartLink.local_id).where(
                SnelstartLink.org_id == self.ctx.org.id,
                SnelstartLink.account_id == account.id,
                SnelstartLink.kind == SnelstartLinkKind.INVOICE.value,
                SnelstartLink.status == SnelstartLinkStatus.ACTIVE.value,
                SnelstartLink.local_id.is_not(None),
            )
            stmt = stmt.where(Invoice.id.not_in(done))

        invoices = list((await self.ctx.session.execute(stmt.limit(500))).scalars())
        client = client_for(account)
        counts = {
            "read": len(invoices), "created": 0, "updated": 0,
            "adopted": 0, "drift": 0, "unchanged": 0, "failed": 0,
        }
        errors: list[dict[str, Any]] = []
        guessed: set[str] = set()

        for invoice in invoices:
            result = await self._row(self.push_invoice(account, invoice, client=client))
            guessed.update(result.guessed_rates)
            if result.ok:
                key = result.action or "unchanged"
                counts[key] = counts.get(key, 0) + 1
            if not result.ok or result.action == "drift":
                # Drift is not a failure — nothing went wrong and nothing needs retrying — but
                # it is a row a human has to look at, so it rides the same list rather than
                # being visible only to somebody who opens the right client's page.
                if not result.ok:
                    counts["failed"] += 1
                if len(errors) < MAX_RUN_ERRORS:
                    errors.append(
                        {
                            "local_id": str(invoice.id),
                            "name": invoice.number,
                            "key": (
                                "errors.snelstart.invoice_differs"
                                if result.action == "drift"
                                else result.error_key
                            ),
                            "message": result.error,
                        }
                    )

        if guessed:
            counts["guessed_rates"] = sorted(guessed)
        account.last_synced_at = datetime.now(UTC)
        return await self.accounts._finish_run(
            run, ok=counts["failed"] == 0, counts=counts, errors=errors
        )

    # ------------------------------------------------------------------ #
    # Payments — the one thing that flows back
    # ------------------------------------------------------------------ #
    async def reconcile_payments(self, account_id: uuid.UUID) -> SnelstartSyncRun:
        """What SnelStart says is still owed, folded back into schakl.

        This is the point of the whole integration for most agencies: the bank statement is
        matched in SnelStart, so SnelStart is the only place that knows an invoice was paid, and
        *"who hasn't paid"* is a CRM question. ``verkoopfacturen.openstaandSaldo`` is the answer
        and it is read wholesale — an invoice becoming paid changes ``modifiedOn`` unreliably,
        and a payment we failed to notice is worse than a read we did not need.

        What lands in schakl is an ordinary :class:`InvoicePayment` (method ``bank``, dated
        today in the org's zone) — **not** a status flipped directly. Everything downstream
        (``_settle``, ``invoice.paid``, the dunning cron, the client portal) then behaves exactly
        as it does for a payment typed in by hand, because as far as it can tell that is what it
        is. A second way of marking an invoice paid is how two screens start disagreeing.

        A **partial** payment is booked too. A client who paid half is not a client who paid, and
        an integration that only recognises "fully settled" leaves the invoice looking untouched.
        """
        from app.modules.invoicing.models import Invoice, InvoiceStatus
        from app.modules.invoicing.schemas import PaymentWrite
        from app.modules.invoicing.service import InvoiceService

        account = await self.accounts.accounts.get_or_404(account_id)
        run = await self.accounts._start_run(account, SnelstartSyncKind.PAYMENTS)
        if not account.pull_payments:
            return await self.accounts._finish_run(
                run, ok=True, counts={"skipped": 1}, message=None
            )
        client = client_for(account)
        try:
            async with self.ctx.release_db():
                remote = await client.fetch_all("verkoopfacturen")
        except SnelstartError as exc:
            return await self.accounts._fail_run(run, account, redact(str(exc))[:500], exc=exc)

        by_number = {
            str(row.get("factuurnummer") or ""): row for row in remote if row.get("factuurnummer")
        }
        open_invoices = list(
            (
                await self.ctx.session.execute(
                    self.ctx.repo(Invoice)
                    .scoped_select()
                    .where(
                        Invoice.status == InvoiceStatus.OPEN.value,
                        Invoice.number.is_not(None),
                    )
                )
            ).scalars()
        )
        service = InvoiceService(self.ctx)
        today = await org_today(self.ctx.session, self.ctx.org.id)
        counts = {"read": len(remote), "checked": len(open_invoices), "booked": 0, "failed": 0}
        errors: list[dict[str, Any]] = []
        rows: list[SnelstartPaymentReconcileRow] = []

        for invoice in open_invoices:
            remote_row = by_number.get(invoice.number or "")
            if remote_row is None:
                continue
            outstanding_remote = parse_amount(remote_row.get("openstaandSaldo"))
            if outstanding_remote is None:
                continue
            local_outstanding = (
                Decimal(invoice.total)
                - Decimal(invoice.paid_total or 0)
                - Decimal(invoice.credited_total or 0)
            )
            delta = local_outstanding - outstanding_remote
            if delta <= 0:
                # SnelStart says at least as much is owed as schakl thinks. Nothing to book —
                # and deliberately no correction the other way: a *negative* delta would mean
                # writing a payment off, which is a human decision about someone's books.
                rows.append(
                    SnelstartPaymentReconcileRow(
                        invoice_id=invoice.id,
                        number=invoice.number or "",
                        outstanding=outstanding_remote,
                        local_outstanding=local_outstanding,
                    )
                )
                continue
            # This one *can* hold a savepoint: ``add_payment`` is pure database work and makes
            # no outbound call, so nothing commits underneath it. See :meth:`_row` for why the
            # push loops cannot.
            savepoint = await self.ctx.session.begin_nested()
            try:
                await service.add_payment(
                    invoice.id,
                    PaymentWrite(
                        paid_on=today,
                        amount=delta,
                        method="bank",
                        note=f"snelstart:{invoice.number}"[:255],
                    ),
                )
                await savepoint.commit()
                counts["booked"] += 1
                rows.append(
                    SnelstartPaymentReconcileRow(
                        invoice_id=invoice.id,
                        number=invoice.number or "",
                        outstanding=outstanding_remote,
                        local_outstanding=local_outstanding,
                        booked=True,
                        amount=delta,
                    )
                )
            except (AppError, SQLAlchemyError) as exc:
                await savepoint.rollback()
                counts["failed"] += 1
                if len(errors) < MAX_RUN_ERRORS:
                    errors.append(
                        {
                            "local_id": str(invoice.id),
                            "name": invoice.number,
                            "key": getattr(exc, "message_key", "errors.snelstart.request_failed"),
                        }
                    )

        account.last_synced_at = datetime.now(UTC)
        return await self.accounts._finish_run(
            run, ok=counts["failed"] == 0, counts=counts, errors=errors
        )

    # ------------------------------------------------------------------ #
    # Articles
    # ------------------------------------------------------------------ #
    async def push_articles(self, account_id: uuid.UUID) -> SnelstartSyncRun:
        """schakl's products into SnelStart's article file.

        Only products that **have a code**: an artikelcode is required (``ART-0002``) and
        inventing one would put a number in somebody's article file that schakl would have to
        keep guessing identically for ever. A product without one is skipped and counted, which
        is what tells an agency to go and fill them in.
        """
        from app.modules.invoicing.models import Product

        account = await self.accounts.accounts.get_or_404(account_id)
        run = await self.accounts._start_run(account, SnelstartSyncKind.ARTICLES)
        group_id = await self._default_revenue_group(account)
        if not group_id:
            return await self.accounts._fail_run(
                run, account, "errors.snelstart.revenue_group_missing"
            )

        products = list(
            (
                await self.ctx.session.execute(
                    self.ctx.repo(Product).scoped_select().order_by(Product.position, Product.name)
                )
            ).scalars()
        )
        client = client_for(account)
        counts = {
            "read": len(products), "created": 0, "updated": 0,
            "unchanged": 0, "skipped": 0, "failed": 0,
        }
        errors: list[dict[str, Any]] = []

        for product in products:
            code = (getattr(product, "code", "") or "").strip()
            if not code:
                counts["skipped"] += 1
                continue
            problem = article_code_error(
                code,
                kind=account.article_code_kind,
                max_length=account.article_code_max_length,
            )
            if problem:
                counts["failed"] += 1
                if len(errors) < MAX_RUN_ERRORS:
                    errors.append(
                        {"local_id": str(product.id), "name": product.name, "key": problem}
                    )
                continue
            result = await self._row(
                self._push_article(account, product, group_id, client=client)
            )
            if result.ok:
                key = result.action or "unchanged"
                counts[key] = counts.get(key, 0) + 1
            else:
                counts["failed"] += 1
                if len(errors) < MAX_RUN_ERRORS:
                    errors.append(
                        {
                            "local_id": str(product.id),
                            "name": product.name,
                            "key": result.error_key,
                        }
                    )

        account.last_synced_at = datetime.now(UTC)
        return await self.accounts._finish_run(
            run, ok=counts["failed"] == 0, counts=counts, errors=errors
        )

    async def _push_article(
        self,
        account: SnelstartAccount,
        product: Any,
        group_id: str,
        *,
        client: SnelstartClient,
    ) -> SnelstartPushResult:
        code = (product.code or "").strip()
        link = await self._link_for(account.id, SnelstartLinkKind.ARTICLE, product.id)
        existing: dict[str, Any] | None = None
        async with self.ctx.release_db():
            if link is not None:
                existing = await client.get("artikelen", link.external_id)
            if existing is None:
                # The code is the identity here, not our link: an article file usually predates
                # the integration, and creating a second row for a code that is already there
                # answers ART-0005 anyway.
                found = await client.fetch(
                    "artikelen",
                    filter_=f"Artikelcode eq {odata_string(code)}",
                    match=lambda row: str(row.get("artikelcode") or "") == code,
                )
                existing = found[0] if found else None

        payload = article_payload(product, revenue_group_id=group_id, existing=existing)
        # Over schakl's own contribution only — see :meth:`push_relation`.
        digest = payload_hash(article_payload(product, revenue_group_id=group_id, existing=None))
        if link is not None and existing is not None and link.push_hash == digest:
            return SnelstartPushResult(
                ok=True, external_id=link.external_id, external_code=code, action="unchanged"
            )
        try:
            async with self.ctx.release_db():
                if existing is not None:
                    external_id = str(existing.get("id") or "")
                    await client.put("artikelen", external_id, payload)
                    action = "updated"
                else:
                    result = await client.post("artikelen", payload) or {}
                    external_id = str(result.get("id") or "")
                    action = "created"
        except SnelstartError as exc:
            return await self._link_failed(
                link, translate(exc).message_key, detail=redact(str(exc))[:500]
            )
        if not external_id:
            return await self._link_failed(link, "errors.snelstart.request_failed")

        now = datetime.now(UTC)
        if link is None:
            link = await self.links.create(
                account_id=account.id,
                kind=SnelstartLinkKind.ARTICLE.value,
                external_id=external_id,
                local_type="product",
                local_id=product.id,
            )
        link.external_id = external_id
        link.external_code = code
        link.external_name = product.name[:255]
        link.push_hash = digest
        link.pushed_at = now
        link.last_synced_at = now
        link.status = SnelstartLinkStatus.ACTIVE.value
        link.last_error = None
        return SnelstartPushResult(
            ok=True, external_id=external_id, external_code=code, action=action
        )

    async def _default_revenue_group(self, account: SnelstartAccount) -> str | None:
        """The ``artikelomzetgroep`` a pushed article lands in.

        Chosen rather than configured, for now: the group whose ledger matches the account's
        default, else the first *services* group (an agency sells services), else the first
        group at all. It decides the article's btw rate, so getting it wrong is expensive — which
        is exactly why the choice is derived from the mapping an admin already made rather than
        being a second, easily-contradicting setting.
        """
        rows = list(
            (
                await self.ctx.session.execute(
                    self.refs.scoped_select()
                    .where(
                        SnelstartRef.account_id == account.id,
                        SnelstartRef.kind == SnelstartRefKind.REVENUE_GROUP.value,
                        SnelstartRef.active.is_(True),
                    )
                    .order_by(SnelstartRef.code)
                )
            ).scalars()
        )
        if not rows:
            return None
        default = await self.accounts.resolve_ledger(account, account.default_ledger_code)
        if default:
            for row in rows:
                ledger = (row.data or {}).get("verkoopGrootboekNederlandIdentifier") or {}
                if str(ledger.get("id") or "") == default[0]:
                    return row.external_id
        for row in rows:
            if "dienst" in (row.name or "").lower():
                return row.external_id
        return rows[0].external_id

    # ------------------------------------------------------------------ #
    # Shared
    # ------------------------------------------------------------------ #
    async def adopt_link(
        self, account_id: uuid.UUID, link_id: uuid.UUID, local_id: uuid.UUID
    ) -> SnelstartLink:
        """Pair a SnelStart row with a schakl record by hand — the reviewer's one click.

        The record is loaded **through its own repository**, so the company horizon (#285) and
        tenant isolation both apply: a restricted staff member cannot pair a relation onto a
        client they cannot see, which would otherwise be a way to learn that client exists.
        """
        link = await self.links.get_or_404(link_id)
        if link.account_id != account_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        # One schakl record pairs with one SnelStart record per account — the partial unique
        # index says so, and without this check it *enforces* it as a 500. That is not a
        # theoretical path: a bookkeeper with the same client entered twice is the ordinary
        # reason somebody opens this screen at all, and the honest answer is "that client is
        # already paired with another relation", not "server error".
        taken = await self.ctx.session.scalar(
            self.links.scoped_select().where(
                SnelstartLink.account_id == account_id,
                SnelstartLink.kind == link.kind,
                SnelstartLink.local_id == local_id,
                SnelstartLink.id != link.id,
            )
        )
        if taken is not None:
            raise AppError(
                "snelstart_already_linked",
                "errors.snelstart.already_linked",
                status_code=409,
                fields={"local_id": "errors.snelstart.already_linked"},
            )
        if link.kind == SnelstartLinkKind.RELATION.value:
            from app.modules.companies.models import Company

            company = await self.ctx.repo(Company).get_or_404(local_id)
            link.local_type = "company"
            link.company_id = company.id
            # What SnelStart holds, not what our screens print: this column mirrors the far
            # side, so it takes the name a push would have written (``app/core/naming.py``).
            link.external_name = link.external_name or document_name_of(company)
        elif link.kind == SnelstartLinkKind.ARTICLE.value:
            from app.modules.invoicing.models import Product

            await self.ctx.repo(Product).get_or_404(local_id)
            link.local_type = "product"
        else:
            raise AppError(
                "snelstart_link_not_adoptable",
                "errors.snelstart.link_not_adoptable",
                status_code=409,
            )
        link.local_id = local_id
        link.status = SnelstartLinkStatus.ACTIVE.value
        # Never claim the payload matches: nothing was pushed, so the next push must write once.
        link.push_hash = None
        link.last_error = None
        await self.ctx.session.flush()
        return link

    async def _links_of(
        self, account_id: uuid.UUID, kind: SnelstartLinkKind
    ) -> list[SnelstartLink]:
        rows = await self.ctx.session.execute(
            self.links.scoped_select().where(
                SnelstartLink.account_id == account_id, SnelstartLink.kind == kind.value
            )
        )
        return list(rows.scalars())

    async def _link_for(
        self, account_id: uuid.UUID, kind: SnelstartLinkKind, local_id: uuid.UUID
    ) -> SnelstartLink | None:
        return await self.ctx.session.scalar(
            self.links.scoped_select().where(
                SnelstartLink.account_id == account_id,
                SnelstartLink.kind == kind.value,
                SnelstartLink.local_id == local_id,
            )
        )

    async def _row(self, work: Any) -> SnelstartPushResult:
        """Run one row of a batch, and never let its failure take the batch down.

        §18 says each row of a bulk write runs in its own ``SAVEPOINT``, and §11 says every
        in-request outbound call is wrapped in ``ctx.release_db()``. **Those two rules cannot
        both hold here**, and finding out which one gives is what this method records:
        ``release_db`` *commits* on entry, and a commit ends the enclosing savepoint — so a
        ``begin_nested()`` around a push is closed by the first HTTP call inside it and its
        eventual ``.commit()`` raises ``ResourceClosedError``. A test caught exactly that.

        ``release_db`` wins, for two reasons. Holding a pooled connection across forty
        outbound calls is the pool-drain §11 exists to prevent, and it is worst precisely in a
        long batch. And the commit it performs turns out to give the thing the savepoint was
        for anyway: **each row's work is already durable before the next row starts**, so a
        failure cannot roll back the rows that succeeded.

        What the savepoint did provide and this must replace is recovery from a *database*
        error, which leaves the session unusable for everything after it. So a
        ``SQLAlchemyError`` rolls the session back — discarding only this row's uncommitted
        work — and the loop carries on. An ``AppError`` is a service deciding to refuse and
        leaves the session fine; it is caught for the same reason §18 catches it, so that "3
        rows skipped" is a vocabulary a service speaks deliberately rather than a bug nobody
        will ever find.
        """
        try:
            return await work
        except AppError as exc:
            return SnelstartPushResult(ok=False, error_key=exc.message_key)
        except SQLAlchemyError:
            logger.exception("snelstart: database error on one row of a batch")
            await self.ctx.session.rollback()
            return SnelstartPushResult(ok=False, error_key="errors.snelstart.request_failed")

    async def _link_failed(
        self, link: SnelstartLink | None, key: str, *, detail: str = ""
    ) -> SnelstartPushResult:
        """Record a per-row failure on its link and return it. **Never raises.**

        The link keeps SnelStart's own untranslatable words and the caller gets the i18n key —
        the §9 split, applied per row so a run's failure list is readable in Dutch while still
        carrying the sentence a support call needs.
        """
        if link is not None:
            link.status = SnelstartLinkStatus.ERROR.value
            link.last_error = (detail or key)[:500]
        return SnelstartPushResult(ok=False, error_key=key, error=detail or None)

    async def _observe(self, link: SnelstartLink, row: dict[str, Any], now: datetime) -> None:
        """Store what SnelStart says this row looks like, keeping our own bookkeeping.

        The private ``_``-prefixed keys we add (the attachment id) are preserved, because they
        are ours and SnelStart will never send them back.
        """
        private = {k: v for k, v in (link.observed or {}).items() if k.startswith("_")}
        link.observed = {**row, **private}
        link.observed_at = parse_moment(row.get("modifiedOn")) or now
        link.external_code = _str(row.get("relatiecode") or row.get("artikelcode"))
        link.external_name = str(row.get("naam") or row.get("omschrijving") or "")[:255] or None
        link.last_synced_at = now

    async def _company_index(self) -> _CompanyIndex:
        """Every company, indexed by the things a relation can be matched on. One query.

        Through ``scoped_select()``, so the company horizon applies: a restricted staff member
        running the review sees pairings only for clients they can see, and the rest read as
        unmatched rather than revealing a name.
        """
        from app.modules.companies.models import Company

        rows = list(
            (
                await self.ctx.session.execute(
                    self.ctx.repo(Company).scoped_select().where(Company.id.is_not(None))
                )
            ).scalars()
        )
        return _CompanyIndex(rows)

    async def _companies_worth_pushing(self, account: SnelstartAccount) -> list[Any]:
        """Companies already paired, plus any with an issued invoice."""
        from app.modules.companies.models import Company
        from app.modules.invoicing.models import Invoice, InvoiceStatus

        invoiced = select(Invoice.company_id).where(
            Invoice.org_id == self.ctx.org.id,
            Invoice.status.in_([InvoiceStatus.OPEN.value, InvoiceStatus.PAID.value]),
        )
        paired = select(SnelstartLink.local_id).where(
            SnelstartLink.org_id == self.ctx.org.id,
            SnelstartLink.account_id == account.id,
            SnelstartLink.kind == SnelstartLinkKind.RELATION.value,
            SnelstartLink.local_id.is_not(None),
        )
        rows = await self.ctx.session.execute(
            self.ctx.repo(Company)
            .scoped_select()
            .where(Company.id.in_(invoiced) | Company.id.in_(paired))
            .order_by(Company.name)
            .limit(1000)
        )
        return list(rows.scalars())

    async def _relation_id_for(
        self, account: SnelstartAccount, invoice: Any, *, client: SnelstartClient
    ) -> str:
        """The SnelStart relation an invoice books against, creating it if it must.

        A boeking without a customer is refused (``BOE-0060`` when the relation is not a
        ``Klant``), so this is a hard prerequisite rather than a nicety — and pushing the client
        as a side effect of pushing their invoice is right, because the alternative is telling
        an admin to run two syncs in the correct order.
        """
        from app.modules.companies.models import Company

        link = await self._link_for(account.id, SnelstartLinkKind.RELATION, invoice.company_id)
        if link is not None and link.external_id and link.status != (
            SnelstartLinkStatus.MISSING.value
        ):
            return link.external_id
        company = await self.ctx.repo(Company).get_or_404(invoice.company_id)
        result = await self.push_relation(account, company, client=client)
        if not result.ok or not result.external_id:
            raise AppError(
                "snelstart_relation_failed",
                result.error_key or "errors.snelstart.relation_failed",
                status_code=409,
            )
        return result.external_id

    async def _primary_contact_name(self, company_id: uuid.UUID) -> str | None:
        """The client's contact person, for the relation's address block.

        Reached through ``contacts``' own tables rather than a join this module invents, and
        deliberately best-effort: a missing contact is not a reason to fail a push, it is a
        blank field on a bookkeeping record.
        """
        try:
            from app.modules.contacts.models import CompanyContact, Contact
        except ImportError:  # pragma: no cover — contacts is an enabled module in practice
            return None
        row = await self.ctx.session.scalar(
            select(Contact.first_name, Contact.last_name)
            .join(CompanyContact, CompanyContact.contact_id == Contact.id)
            .where(
                CompanyContact.company_id == company_id,
                CompanyContact.org_id == self.ctx.org.id,
            )
            .order_by(CompanyContact.is_primary.desc())
            .limit(1)
        )
        if row is None:
            return None
        parts = [part for part in (row[0], row[1]) if part]
        return " ".join(parts) or None


class _CompanyIndex:
    """Companies keyed by every identifier a SnelStart relation might carry."""

    def __init__(self, rows: Sequence[Any]) -> None:
        self.names: dict[uuid.UUID, str] = {row.id: row.name for row in rows}
        self.by_coc: dict[str, uuid.UUID] = {}
        self.by_vat: dict[str, uuid.UUID] = {}
        self.by_client_number: dict[str, uuid.UUID] = {}
        self.by_email: dict[str, uuid.UUID] = {}
        self.by_name: dict[str, uuid.UUID] = {}
        for row in rows:
            _put(self.by_coc, _digits(getattr(row, "coc_number", None)), row.id)
            _put(self.by_vat, _norm(getattr(row, "vat_number", None)), row.id)
            _put(self.by_client_number, _digits(getattr(row, "client_number", None)), row.id)
            _put(self.by_email, _norm(getattr(row, "invoice_email", None)), row.id)
            # Both names (``app/core/naming.py``): a relation SnelStart holds was named by
            # whoever typed it there — the bookkeeper, who uses the legal entity — while a
            # relation *we* pushed carries the legal name too. Indexing only the label made the
            # name tier miss exactly the clients it is most likely to be asked about. `_put`
            # still collapses a key two clients share, so widening the index cannot make a
            # wrong match, only fewer missed ones.
            _put(self.by_name, _norm(row.name), row.id)
            _put(self.by_name, _norm(getattr(row, "legal_name", None)), row.id)


def _put(index: dict[str, uuid.UUID], key: str | None, value: uuid.UUID) -> None:
    """First one wins, and a **duplicate removes the key entirely**.

    Two companies sharing a VAT number cannot be told apart by it, so the identifier stops being
    an identifier — silently picking whichever was loaded first is how an invoice goes to the
    wrong company with nothing on any screen to suggest it.
    """
    if not key:
        return
    if key in index and index[key] != value:
        index[key] = _AMBIGUOUS
        return
    index.setdefault(key, value)


_AMBIGUOUS = uuid.UUID(int=0)


def _match_company(
    row: dict[str, Any], index: _CompanyIndex
) -> tuple[uuid.UUID | None, str | None]:
    """Which schakl company this relation is, and how confidently.

    Ordered strongest first. ``coc`` and ``vat`` identify a legal entity; ``client_number`` is
    a number two systems agreed on; ``email`` is strong but shared by a holding's subsidiaries;
    ``name`` is a guess and is never applied automatically.
    """
    for key, source, label in (
        (_digits(row.get("kvkNummer")), index.by_coc, "coc"),
        (_norm(row.get("btwNummer")), index.by_vat, "vat"),
        (_digits(row.get("relatiecode")), index.by_client_number, "client_number"),
        (_norm(row.get("email")), index.by_email, "email"),
        (_norm(row.get("naam")), index.by_name, "name"),
    ):
        if not key:
            continue
        found = source.get(key)
        if found and found != _AMBIGUOUS:
            return found, label
    return None, None


def _same_company(relation: Mapping[str, Any], company: Any) -> bool:
    """Is this SnelStart relation the schakl company we were about to create?

    Only the identifiers that identify, and an **exact** name — the same rule
    :func:`_match_company` applies, minus the "proposed" tier: nothing here reaches a human for
    review, so a guess would be applied silently, which is the one thing matching must never do.
    """
    for key, attr in (("kvkNummer", "coc_number"), ("btwNummer", "vat_number")):
        remote = _digits(relation.get(key)) if key == "kvkNummer" else _norm(relation.get(key))
        local = (
            _digits(getattr(company, attr, None))
            if key == "kvkNummer"
            else _norm(getattr(company, attr, None))
        )
        if remote and local:
            return remote == local
    # Against the name we would have *pushed* (the legal one where there is one), and against
    # the label as well: an agency that created the relation by hand before connecting the
    # integration typed whichever of the two it thinks in.
    remote_name = _norm(relation.get("naam"))
    return remote_name is not None and remote_name in {
        _norm(document_name_of(company)),
        _norm(getattr(company, "name", None)),
    }


def _norm(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _digits(value: Any) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.lstrip("0") or None if digits else None


def _str(value: Any) -> str | None:
    text = str(value).strip() if value not in (None, "") else ""
    return text or None
