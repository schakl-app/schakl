"""Downloading a *selection* of invoices as one zip (issue #307).

The bulk half of ``GET /invoices/{id}/pdf``, and the four properties that make it more than a
loop around it:

* it is **the same document** the single download hands out — an archived invoice and a
  downloaded one can never disagree;
* the selection rides the scoped repository, so a row this caller may not read is **absent**
  rather than an error that would confirm it exists, and an archive of nothing is a 404;
* it **caps** what one request will render, because every entry is a full WeasyPrint layout;
* and it reads the org's identity **once**, not once per invoice — the property that is
  invisible in the response and is the whole reason this is a batch method (§9,
  docs/PERFORMANCE.md).
"""

from __future__ import annotations

import io
import zipfile

from app.modules.invoicing.router import MAX_ARCHIVE_DOCUMENTS
from tests.conftest import Tenant, auth_cookie, make_tenant, org_today
from tests.test_invoicing_api import _setup_org

#: The API dates on the org's calendar, so the expectations must too (`conftest.org_today`).
_today = org_today

_PDF = "/api/v1/invoicing/invoices/pdf"


async def _company(client, headers, name: str) -> str:
    resp = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _invoice(client, headers, company_id: str, description: str) -> dict:
    resp = await client.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": company_id,
            "lines": [{"description": description, "quantity": "1", "unit_price": "125"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _entries(payload: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            assert archive.read(name).startswith(b"%PDF"), f"{name} is not a PDF"
        return sorted(archive.namelist())


async def test_a_selection_downloads_as_one_zip_of_the_same_pdfs(client_for) -> None:
    """Three picked invoices come back as three files, byte-identical to the single download.

    The identity check is the point of asserting it at all: the archive must not grow a second
    renderer with its own opinion about what an invoice looks like.
    """
    t: Tenant = await make_tenant("inv-zip")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers, "Zip BV")
        invoices = [await _invoice(c, headers, company, f"Sprint {n}") for n in range(3)]
        issued = (
            await c.post(
                f"/api/v1/invoicing/invoices/{invoices[0]['id']}/issue", json={}, headers=headers
            )
        ).json()
        # Part-paid, because the identity check is worth nothing against a document whose money
        # fields are all zero. "Reeds betaald" and "te betalen" are exactly where a batch read
        # that skipped something would show — they come from columns (#290), which is *why* the
        # archive can be the same document without loading each invoice's payment rows.
        assert (
            await c.post(
                f"/api/v1/invoicing/invoices/{issued['id']}/payments",
                json={"paid_on": _today().isoformat(), "amount": "50"},
                headers=headers,
            )
        ).status_code in (200, 201)

        res = await c.get(_PDF, params={"ids": [i["id"] for i in invoices]}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.headers["content-type"] == "application/zip"
        assert "attachment" in res.headers["content-disposition"]
        assert res.headers["content-disposition"].endswith('.zip"')

        names = _entries(res.content)
        assert len(names) == 3
        # An issued invoice is filed under its number; a draft keeps the id it has instead —
        # both unique, which is what makes a zip a safe container for a mixed selection.
        assert f"{issued['number']}.pdf" in names
        assert sorted(n for n in names if n != f"{issued['number']}.pdf") == sorted(
            f"factuur-{i['id']}.pdf" for i in invoices[1:]
        )

        # Every entry, not just the issued one: "the same document" has to hold for the draft
        # that has no number as much as for the part-paid invoice that does.
        with zipfile.ZipFile(io.BytesIO(res.content)) as archive:
            for invoice, name in ((issued, f"{issued['number']}.pdf"), *(
                (i, f"factuur-{i['id']}.pdf") for i in invoices[1:]
            )):
                single = await c.get(
                    f"/api/v1/invoicing/invoices/{invoice['id']}/pdf", headers=headers
                )
                assert single.status_code == 200, single.text
                assert archive.read(name) == single.content, name


async def test_an_id_the_caller_cannot_read_is_absent_and_an_empty_archive_is_a_404(
    client_for,
) -> None:
    """Tenant isolation, and the answer when the selection resolves to nothing.

    A cross-tenant id is simply not in the archive — the repository's clause never matched it,
    and there is no 403 to leak that it is somebody's invoice. When *nothing* resolves there is
    no archive to send, so the request is a 404 rather than an empty zip pretending to be one.
    """
    mine: Tenant = await make_tenant("inv-zip-mine")
    theirs: Tenant = await make_tenant("inv-zip-theirs")
    my_headers = await auth_cookie(mine.user)
    their_headers = await auth_cookie(theirs.user)

    async with client_for(theirs.host) as c:
        await _setup_org(c, their_headers)
        foreign = await _invoice(
            c, their_headers, await _company(c, their_headers, "Andere BV"), "Niet van jou"
        )

    async with client_for(mine.host) as c:
        await _setup_org(c, my_headers)
        ours = await _invoice(c, my_headers, await _company(c, my_headers, "Mijn BV"), "Werk")

        mixed = await c.get(
            _PDF, params={"ids": [ours["id"], foreign["id"]]}, headers=my_headers
        )
        assert mixed.status_code == 200, mixed.text
        assert len(_entries(mixed.content)) == 1

        assert (
            await c.get(_PDF, params={"ids": [foreign["id"]]}, headers=my_headers)
        ).status_code == 404


async def test_the_archive_refuses_more_than_it_will_render(client_for) -> None:
    """The cap is declared on the route, so an over-long selection is refused before a single
    layout runs — the failure mode a synchronous batch has to have (``MAX_IMPORT_ROWS``' rule).
    """
    t: Tenant = await make_tenant("inv-zip-cap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers, "Cap BV")
        one = await _invoice(c, headers, company, "Werk")
        too_many = [one["id"]] * (MAX_ARCHIVE_DOCUMENTS + 1)
        assert (await c.get(_PDF, params={"ids": too_many}, headers=headers)).status_code == 422
        # Exactly the cap still passes, so the boundary is the documented one and not one off it.
        assert (
            await c.get(
                _PDF, params={"ids": [one["id"]] * MAX_ARCHIVE_DOCUMENTS}, headers=headers
            )
        ).status_code == 200


async def test_the_archive_costs_the_same_at_five_documents_as_at_one(
    client_for, count_queries
) -> None:
    """The shape, not the timing (``QueryCounter``'s own rule): **nothing** here is per row.

    The seller block, the org's branding and its logo bytes answer the same for every invoice
    in the batch and are read once; the design comes from one memo per template; the lines are
    one grouped read. So a five-document archive issues exactly the statements a one-document
    archive does — which is the property the response cannot show you, and the one that made
    this a batch method instead of a loop around ``document_pdf``.
    """
    t: Tenant = await make_tenant("inv-zip-budget")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers, "Budget BV")
        ids = [(await _invoice(c, headers, company, f"Regel {n}"))["id"] for n in range(5)]

        with count_queries() as one:
            first = await c.get(_PDF, params={"ids": ids[:1]}, headers=headers)
        with count_queries() as five:
            all_five = await c.get(_PDF, params={"ids": ids}, headers=headers)

        assert first.status_code == 200 and all_five.status_code == 200, all_five.text
        assert len(_entries(all_five.content)) == 5
        assert len(five) == len(one), (
            "the archive grew with its selection:\n  "
            + "\n  ".join(sorted(set(five.statements) - set(one.statements)))
        )
        # Named as well as counted, so a future rewrite that keeps the total while moving the
        # per-row work elsewhere still fails here.
        assert len(five.matching("org_settings.brand_name")) == 1
        assert len(five.matching("FROM invoicing_settings")) == 1
        assert len(five.matching("FROM invoice_lines")) == 1
