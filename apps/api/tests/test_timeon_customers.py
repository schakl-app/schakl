"""The client half of the Timeon sync (``docs/TIMEON.md`` §5b).

Clients were *paired* — on their number — and nothing else: a client made in schakl never reached
Timeon, a customer made in Timeon was a warning on every run, and a corrected address stayed
corrected on one side. Each test here is one thing that was either missing or would be expensive
to get wrong, and the fake saves customers wholesale and serves only the keys a row was given,
because those are the two facts about the live API a lazier fake would hide.
"""

from __future__ import annotations

import pytest

from app.integrations.timeon import client as timeon_client
from app.integrations.timeon.customer_mapping import split_address
from app.integrations.timeon.models import TimeonLinkKind, TimeonLinkOrigin
from tests.test_timeon_sync import (
    API_KEY,
    TIMEON_CUSTOMER,
    _company,
    _connect,
    _links,
    _seed_remote,
    _sync,
    _tenant,
)
from tests.timeon_fake import FakeTimeon


@pytest.fixture
def timeon() -> FakeTimeon:
    fake = FakeTimeon()
    fake.api_key = API_KEY
    timeon_client.set_transport(fake.transport())
    yield fake
    timeon_client.set_transport(None)


PULL = {"customers_direction": "pull", "create_missing_customers": True}
PUSH = {"customers_direction": "push", "create_missing_customers": True}
TWO_WAY = {"customers_direction": "two_way", "create_missing_customers": True}

#: A customer row as Timeon's list serves one: every field the sync compares.
FULL_ROW = {
    "isActive": True,
    "emailAddress": "facturen@klant.nl",
    "phoneNumber": "0118 123456",
    "addressLine1": "Dorpsstraat 12",
    "addressLine2": "4331 AB",
    "addressLine3": "Middelburg",
    "countryID": 1,
    "vatNumber": "NL 8123.45.678.B01",
    "remark": "Belt liever niet voor tienen",
}


async def _companies(client, headers) -> list[dict]:
    body = (await client.get("/api/v1/companies", headers=headers)).json()
    return body["items"] if isinstance(body, dict) else body


async def _company_full(client, headers, **fields) -> dict:
    resp = await client.post("/api/v1/companies", json=fields, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_company(client, headers, company_id: str, **fields) -> None:
    resp = await client.patch(f"/api/v1/companies/{company_id}", json=fields, headers=headers)
    assert resp.status_code == 200, resp.text


def _warned(run: dict, code: str) -> list[dict]:
    return [w for w in run["warnings"] if w["code"] == code]


# --------------------------------------------------------------------------------------- #
# What an upgrade must not change
# --------------------------------------------------------------------------------------- #
async def test_with_the_direction_off_a_client_is_paired_and_nothing_is_written(
    client_for, timeon
) -> None:
    """The behaviour every existing connection upgrades into. ``off`` is the default."""
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant BV (Timeon)", "402148", **FULL_ROW)
    timeon.add_customer(2112238, "Alleen in Timeon", "900001", **FULL_ROW)
    company_id = await _company(client, headers)
    await _company_full(client, headers, name="Alleen hier", client_number="900002")
    account_id = await _connect(client, headers, timeon, create_missing_customers=True)

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run

    links = await _links(tenant, TimeonLinkKind.CUSTOMER.value)
    assert [(str(link.local_id), link.external_id) for link in links] == [
        (company_id, str(TIMEON_CUSTOMER))
    ]
    assert len(_warned(run, "customer_unmapped")) == 1
    assert {c["name"] for c in await _companies(client, headers)} == {"Klant BV", "Alleen hier"}
    assert len(timeon.customers) == 2
    assert not [call for call in timeon.calls if call[1] == "/api/customer/save"]


# --------------------------------------------------------------------------------------- #
# Creating what one side has never seen
# --------------------------------------------------------------------------------------- #
async def test_a_client_made_here_is_created_in_timeon(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    company = await _company_full(
        client,
        headers,
        name="Nova Fietsen",
        client_number="0042",
        invoice_email="facturen@nova.nl",
        phone="+31118123456",
        address_line1="Dorpsstraat",
        house_number="12",
        postal_code="4331 AB",
        city="Middelburg",
        country="NL",
    )
    account_id = await _connect(client, headers, timeon, **PUSH)

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    assert run["counts"]["customers_pushed_new"] == 1

    row = timeon.customers[0]
    assert row["name"] == "Nova Fietsen"
    assert row["customerNumber"] == "0042"
    assert row["emailAddress"] == "facturen@nova.nl"
    assert row["phoneNumber"] == "+31118123456"
    # Timeon's three lines are a street, a postal code and a city.
    assert (row["addressLine1"], row["addressLine2"], row["addressLine3"]) == (
        "Dorpsstraat 12",
        "4331 AB",
        "Middelburg",
    )
    assert row["countryID"] == 1

    link = (await _links(tenant, TimeonLinkKind.CUSTOMER.value))[0]
    assert str(link.local_id) == company["id"]
    assert link.origin == TimeonLinkOrigin.SCHAKL.value

    again = await _sync(client, headers, account_id, kind="full")
    assert "customers_pushed_new" not in again["counts"]
    assert len(timeon.customers) == 1


async def test_a_project_under_a_new_client_goes_over_in_the_same_run(
    client_for, timeon
) -> None:
    """Why this matters beyond the register: a project could not be created in Timeon under a
    client Timeon had never heard of, and said ``project_no_customer`` instead."""
    tenant, headers, client = await _tenant(client_for)
    company = await _company_full(client, headers, name="Nova Fietsen", client_number="0042")
    resp = await client.post(
        "/api/v1/projects",
        json={"company_id": company["id"], "name": "Webshop"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    account_id = await _connect(
        client,
        headers,
        timeon,
        projects_direction="push",
        create_missing_projects=True,
        **PUSH,
    )

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    assert not _warned(run, "project_no_customer")
    assert timeon.projects[0]["customerID"] == timeon.customers[0]["customerID"]


async def test_a_customer_made_in_timeon_is_created_here(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Bakkerij Jansen", "402200", **FULL_ROW)
    account_id = await _connect(client, headers, timeon, **PULL)

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    assert run["counts"]["customers_created"] == 1

    company = next(c for c in await _companies(client, headers) if c["name"] == "Bakkerij Jansen")
    assert company["client_number"] == "402200"
    assert company["invoice_email"] == "facturen@klant.nl"
    assert company["phone"] == "+31118123456"
    assert (company["address_line1"], company["house_number"]) == ("Dorpsstraat", "12")
    assert (company["postal_code"], company["city"], company["country"]) == (
        "4331 AB",
        "Middelburg",
        "NL",
    )
    assert company["vat_number"] == "NL 8123.45.678.B01"
    assert company["status"] == "active"

    again = await _sync(client, headers, account_id, kind="full")
    assert "customers_created" not in again["counts"]
    assert again["counts"]["customers_in_step"] == 1
    assert len(await _companies(client, headers)) == 1


async def test_a_person_is_created_here_under_their_own_name(client_for, timeon) -> None:
    """Timeon's customer is a company or a person; a person has no ``name`` at all."""
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(
        TIMEON_CUSTOMER, None, "402201", firstname="Piet", lastname="de Vries", isActive=True
    )
    account_id = await _connect(client, headers, timeon, **TWO_WAY)

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    assert [c["name"] for c in await _companies(client, headers)] == ["Piet de Vries"]

    # …and the name is never written back: that would turn the person into a company.
    await _sync(client, headers, account_id, kind="full")
    assert timeon.customers[0]["name"] is None


async def test_a_dry_run_counts_and_creates_nothing(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Bakkerij Jansen", "402200", **FULL_ROW)
    await _company_full(client, headers, name="Nova Fietsen", client_number="0042")
    account_id = await _connect(client, headers, timeon, **TWO_WAY)

    run = await _sync(client, headers, account_id, kind="full", dry_run=True)
    assert run["counts"]["customers_created"] == 1
    assert run["counts"]["customers_pushed_new"] == 1
    assert len(timeon.customers) == 1
    assert len(await _companies(client, headers)) == 1
    assert not await _links(tenant, TimeonLinkKind.CUSTOMER.value)


async def test_without_the_switch_a_missing_client_is_named_not_made(
    client_for, timeon
) -> None:
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Bakkerij Jansen", "402200", **FULL_ROW)
    await _company_full(client, headers, name="Nova Fietsen", client_number="0042")
    account_id = await _connect(client, headers, timeon, customers_direction="two_way")

    run = await _sync(client, headers, account_id, kind="full")
    assert len(_warned(run, "customer_unmapped")) == 1
    assert len(timeon.customers) == 1
    assert len(await _companies(client, headers)) == 1


async def test_an_archived_client_is_not_sent(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _company_full(client, headers, name="Oud", client_number="0001", status="archived")
    account_id = await _connect(client, headers, timeon, **PUSH)

    run = await _sync(client, headers, account_id, kind="full")
    assert "customers_pushed_new" not in run["counts"]
    assert timeon.customers == []


# --------------------------------------------------------------------------------------- #
# Recognising what is already on both sides
# --------------------------------------------------------------------------------------- #
async def test_a_name_pairs_two_clients_only_where_it_is_unique_on_both_sides(
    client_for, timeon
) -> None:
    """*Maatschap Mini Camping Boudewijnskerke* exists twice in both systems."""
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(1, "Uniek BV", None, isActive=True)
    timeon.add_customer(2, "Dubbel", None, isActive=True)
    timeon.add_customer(3, "Dubbel", None, isActive=True)
    unique = await _company_full(client, headers, name="Uniek BV")
    await _company_full(client, headers, name="Dubbel")
    account_id = await _connect(client, headers, timeon, customers_direction="pull")

    run = await _sync(client, headers, account_id, kind="full")
    links = await _links(tenant, TimeonLinkKind.CUSTOMER.value)
    assert [(str(link.local_id), link.external_id) for link in links] == [(unique["id"], "1")]
    assert len(_warned(run, "customer_unmapped")) == 2


async def test_a_stored_pairing_outranks_the_number(client_for, timeon) -> None:
    """Correcting a client number used to read as "unknown client" on the next run."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    account_id = await _connect(client, headers, timeon, customers_direction="push")
    await _sync(client, headers, account_id, kind="full")

    await _patch_company(client, headers, company_id, client_number="500000")
    run = await _sync(client, headers, account_id, kind="full")
    assert not _warned(run, "customer_unmapped")
    assert timeon.customer(TIMEON_CUSTOMER)["customerNumber"] == "500000"


async def test_a_client_deleted_here_is_reported_and_not_made_again(
    client_for, timeon
) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    timeon.projects.clear()
    company_id = await _company(client, headers)
    account_id = await _connect(client, headers, timeon, **TWO_WAY)
    await _sync(client, headers, account_id, kind="full")

    resp = await client.delete(f"/api/v1/companies/{company_id}", headers=headers)
    assert resp.status_code in (200, 204), resp.text

    run = await _sync(client, headers, account_id, kind="full")
    assert len(_warned(run, "customer_gone_here")) == 1, run
    assert "customers_created" not in run["counts"]
    assert await _companies(client, headers) == []


# --------------------------------------------------------------------------------------- #
# Keeping the fields in step
# --------------------------------------------------------------------------------------- #
async def test_an_edit_here_reaches_timeon_and_carries_what_schakl_has_no_field_for(
    client_for, timeon
) -> None:
    """``customer/save`` replaces: the remark is Timeon's alone and must survive a rename."""
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant BV", "402148", **FULL_ROW)
    account_id = await _connect(client, headers, timeon, **TWO_WAY)
    await _sync(client, headers, account_id, kind="full")
    company = (await _companies(client, headers))[0]

    await _patch_company(client, headers, company["id"], name="Klant Groep BV", city="Vlissingen")
    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    assert run["counts"]["customers_pushed"] == 1

    row = timeon.customer(TIMEON_CUSTOMER)
    assert (row["name"], row["addressLine3"]) == ("Klant Groep BV", "Vlissingen")
    assert row["remark"] == "Belt liever niet voor tienen"
    assert row["vatNumber"] == "NL 8123.45.678.B01"

    again = await _sync(client, headers, account_id, kind="full")
    assert again["counts"]["customers_in_step"] == 1
    assert "customers_pushed" not in again["counts"]


async def test_an_edit_in_timeon_reaches_the_client_here(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant BV", "402148", **FULL_ROW)
    account_id = await _connect(client, headers, timeon, **TWO_WAY)
    await _sync(client, headers, account_id, kind="full")

    row = timeon.customer(TIMEON_CUSTOMER)
    row["addressLine1"], row["emailAddress"], row["isActive"] = "Markt 1", "info@klant.nl", False
    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    assert run["counts"]["customers_pulled"] == 1

    company = (await _companies(client, headers))[0]
    assert (company["address_line1"], company["house_number"]) == ("Markt", "1")
    assert company["invoice_email"] == "info@klant.nl"
    assert company["status"] == "archived"


async def test_two_people_changing_two_fields_both_land(client_for, timeon) -> None:
    """Per field, not per client: a phone number here and a city there are two changes."""
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant BV", "402148", **FULL_ROW)
    account_id = await _connect(client, headers, timeon, **TWO_WAY)
    await _sync(client, headers, account_id, kind="full")
    company = (await _companies(client, headers))[0]

    await _patch_company(client, headers, company["id"], phone="+31612345678")
    timeon.customer(TIMEON_CUSTOMER)["addressLine3"] = "Goes"
    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run

    assert timeon.customer(TIMEON_CUSTOMER)["phoneNumber"] == "+31612345678"
    assert timeon.customer(TIMEON_CUSTOMER)["addressLine3"] == "Goes"
    assert (await _companies(client, headers))[0]["city"] == "Goes"


async def test_a_pairing_with_no_record_is_asked_about_and_left_alone(
    client_for, timeon
) -> None:
    """Every pairing made before clients were synced: nobody can say who moved."""
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant B.V.", "402148", isActive=True)
    company_id = await _company(client, headers)
    account_id = await _connect(client, headers, timeon, customers_direction="two_way")

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    asked = _warned(run, "customer_differs_name")
    assert [(w["schakl"], w["timeon"]) for w in asked] == [("Klant BV", "Klant B.V.")]
    assert timeon.customer(TIMEON_CUSTOMER)["name"] == "Klant B.V."

    # The run offers the two honest answers; ``customers`` settles clients and nothing else.
    settled = await _sync(client, headers, account_id, kind="customers", prefer="schakl")
    assert not _warned(settled, "customer_differs_name")
    assert timeon.customer(TIMEON_CUSTOMER)["name"] == "Klant BV"
    names = [c["name"] for c in await _companies(client, headers)]
    assert names == ["Klant BV"], company_id


async def test_a_blank_is_filled_and_never_fills(client_for, timeon) -> None:
    """A one-way pull that read "Timeon has no e-mail address" as an instruction would wipe the
    invoice address off every client on its first run."""
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(
        TIMEON_CUSTOMER,
        "Klant BV",
        "402148",
        isActive=True,
        emailAddress=None,
        addressLine3="Middelburg",
    )
    company = await _company_full(
        client,
        headers,
        name="Klant BV",
        client_number="402148",
        invoice_email="facturen@klant.nl",
    )
    account_id = await _connect(client, headers, timeon, customers_direction="pull")

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    after = next(c for c in await _companies(client, headers) if c["id"] == company["id"])
    assert after["invoice_email"] == "facturen@klant.nl"
    assert after["city"] == "Middelburg"


async def test_a_field_the_list_does_not_carry_is_unknown_not_empty(
    client_for, timeon
) -> None:
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant BV", "402148")
    await _company_full(
        client,
        headers,
        name="Klant BV",
        client_number="402148",
        vat_number="NL812345678B01",
        city="Middelburg",
    )
    account_id = await _connect(client, headers, timeon, customers_direction="two_way")

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    assert run["counts"]["customers_in_step"] == 1
    assert not run["warnings"]
    assert "vatNumber" not in timeon.customer(TIMEON_CUSTOMER)


async def test_a_number_another_client_holds_is_one_line_not_the_end_of_the_run(
    client_for, timeon
) -> None:
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(1, "Eerste", "100", isActive=True)
    timeon.add_customer(2, "Tweede", "200", isActive=True)
    account_id = await _connect(client, headers, timeon, **PULL)
    await _sync(client, headers, account_id, kind="full")

    timeon.customer(2)["customerNumber"] = "100"
    timeon.customer(1)["addressLine3"] = "Goes"
    run = await _sync(client, headers, account_id, kind="full")
    assert [e["code"] for e in run["errors"]] == ["customer_pull_failed"]
    assert "errors.companies.client_number_taken" in run["errors"][0]["detail"]
    by_name = {c["name"]: c for c in await _companies(client, headers)}
    assert by_name["Tweede"]["client_number"] == "200"
    assert by_name["Eerste"]["city"] == "Goes"


async def test_an_hours_run_pairs_clients_and_writes_nothing_about_them(
    client_for, timeon
) -> None:
    tenant, headers, client = await _tenant(client_for)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant B.V.", "402148", isActive=True)
    timeon.add_customer(2, "Alleen in Timeon", "900001", isActive=True)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, **TWO_WAY)

    run = await _sync(client, headers, account_id, kind="hours", prefer="schakl")
    assert run["counts"]["customer_paired"] == 1
    assert timeon.customer(TIMEON_CUSTOMER)["name"] == "Klant B.V."
    assert len(await _companies(client, headers)) == 1


def test_a_street_line_is_split_only_where_it_ends_in_a_house_number() -> None:
    assert split_address("Dorpsstraat 12") == ("Dorpsstraat", "12")
    assert split_address("Lange Noordstraat 4-6") == ("Lange Noordstraat", "4-6")
    assert split_address("Plein 1940 12a") == ("Plein 1940", "12a")
    assert split_address("Kade 12 bis") == ("Kade 12 bis", None)
    assert split_address("Postbus") == ("Postbus", None)
    assert split_address("") == (None, None)
