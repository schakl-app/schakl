"""A scriptable stand-in for the SnelStart B2B-API v2 (epic #377, issue #31).

Modelled on :mod:`tests.mollie_fake` and :mod:`tests.oxxa_fake` — state in plain dicts, a
recorded call log, and a way to make one call fail. What makes this one its own animal is that
**it reproduces the live API's misbehaviour, not the documentation's behaviour**, and every
divergence below was measured against a real administration before it was written down. A fake
that is kinder than the real server is a fake the bug hides in.

Four of those, and each is a hazard this file exists to keep honest:

* **``$filter`` is honoured by some endpoints and silently ignored by others.** ``/relaties``,
  ``/grootboeken`` and ``/artikelen`` apply it and *reject an unknown property*; ``/landen`` and
  ``/dagboeken`` answer ``200`` with the whole list whatever you ask. That is real — a live
  ``?$filter=Nonsense eq 'x'`` against ``/landen`` returns all 250 countries — and it is the
  reason ``client.fetch`` takes a ``match`` predicate at all. A fake that filtered everything
  would let a client that trusted the server pass every test and pick Nederland for Estonia in
  production.

* **There is no paging metadata.** No ``nextLink``, no count, max 500 rows. This fake pages the
  same way, so :meth:`SnelstartClient.fetch_all`'s "ask again only while the page came back
  full" is actually exercised rather than assumed.

* **Errors are an array of ``{errorCode, message, details}``**, and the code carries the
  meaning. ``BOE-0021`` (*"Het factuurnummer bestaat al"*) is raised for a duplicate
  ``factuurnummer`` **by this fake, automatically**, because that is what the live API does and
  it is the single most important behaviour in the whole integration: it is how "have I already
  pushed this invoice?" is answered, and a fake that let a duplicate through would make the
  idempotency test pass against an API that does not exist.

* **The credential is in a POST body and in a header, so this fake records neither.** Only the
  method, the path, the query and the JSON body. ``test_the_fake_never_records_a_credential``
  asserts it — a harness that logged the whole request would put the tenant's koppelsleutel in
  every pytest failure output, which is the leak ``redact`` exists to prevent, reintroduced one
  layer down.

Money crosses this boundary the way it crosses the real one: it is **sent** as a decimal string
(``"1428.00"``) so no float ever exists on our side, and .NET parses that into a ``decimal``
exactly — verified live, where a boeking posted with string amounts is accepted and read back
with numeric ones. :func:`_money` reproduces that normalisation, because a fake that stored the
string it was handed would let a test pass while asserting against a shape the real API never
returns.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

#: SnelStart's versioned base path. ``client.API_BASE`` ends in it.
PREFIX = "/v2"

#: The token endpoint, which is a *different host* from the API and takes no subscription key.
TOKEN_PATH = "/b2b/token"

#: Endpoints that really apply ``$filter``. Everything else ignores it — see the module
#: docstring. Kept as data rather than an ``if`` so a reader can see the whole divide at once.
FILTERING = frozenset({"relaties", "grootboeken", "artikelen", "verkoopfacturen"})

#: The Dutch btw schedule as the live API returns it, trimmed to the rows that matter. Both
#: low-rate rows are kept on purpose: 6% until 2018 and 9% from 2019 is precisely the case a
#: hardcoded constant would get wrong, and the date-window lookup is what this proves.
BTW_TARIEVEN: list[dict[str, Any]] = [
    {
        "btwSoort": "Geen",
        "btwPercentage": 0.0,
        "datumVanaf": "1900-01-01T00:00:00",
        "datumTotEnMet": "2400-12-31T00:00:00",
    },
    {
        "btwSoort": "Laag",
        "btwPercentage": 9.0,
        "datumVanaf": "2019-01-01T00:00:00",
        "datumTotEnMet": "2400-12-31T00:00:00",
    },
    {
        "btwSoort": "Laag",
        "btwPercentage": 6.0,
        "datumVanaf": "1986-10-01T00:00:00",
        "datumTotEnMet": "2018-12-31T00:00:00",
    },
    {
        "btwSoort": "Hoog",
        "btwPercentage": 21.0,
        "datumVanaf": "2012-10-01T00:00:00",
        "datumTotEnMet": "2400-12-31T00:00:00",
    },
    {
        "btwSoort": "Hoog",
        "btwPercentage": 19.0,
        "datumVanaf": "2001-01-01T00:00:00",
        "datumTotEnMet": "2012-09-30T00:00:00",
    },
    {
        "btwSoort": "Overig",
        "btwPercentage": 0.0,
        "datumVanaf": "1968-01-01T00:00:00",
        "datumTotEnMet": "2400-12-31T00:00:00",
    },
]

#: A slice of the seeded chart of accounts, with the real uuids' shape and the real
#: ``grootboekfunctie`` values. Only revenue accounts plus one that is deliberately *not* one,
#: so the ledger picker's narrowing has something to exclude.
GROOTBOEKEN: list[dict[str, Any]] = [
    {
        "id": "7a7b3888-7620-47c0-b248-98424219771c",
        "nummer": 8200,
        "omschrijving": "Omzet hoog (diensten)",
        "grootboekfunctie": "VerkopenOmzetHoog",
        "btwSoort": ["Hoog"],
        "rekeningCode": "WinstEnVerlies",
        "nonactief": False,
    },
    {
        "id": "51fdb7b3-0ddb-420f-858f-7b78280eeb49",
        "nummer": 8210,
        "omschrijving": "Omzet laag (diensten)",
        "grootboekfunctie": "VerkopenOmzetLaag",
        "btwSoort": ["Laag"],
        "rekeningCode": "WinstEnVerlies",
        "nonactief": False,
    },
    {
        "id": "24011ea6-7bd8-42d6-aff3-78dc3536ca5d",
        "nummer": 8250,
        "omschrijving": "Omzet verlegd (diensten)",
        "grootboekfunctie": "VerkopenOmzetOnbelastVerlegd",
        "btwSoort": ["Geen"],
        "rekeningCode": "WinstEnVerlies",
        "nonactief": False,
    },
    {
        # Not a revenue account. The picker must never offer it: booking an invoice line to
        # "btw af te dragen" produces a boeking that balances and means nothing.
        "id": "9c0f1e77-1111-4a55-9a11-0f1e77aabbcc",
        "nummer": 1671,
        "omschrijving": "Btw af te dragen hoog (verkopen)",
        "grootboekfunctie": "BtwAfTeDragenHoog",
        "btwSoort": ["Geen"],
        "rekeningCode": "Balans",
        "nonactief": False,
    },
]

DAGBOEKEN: list[dict[str, Any]] = [
    {
        "id": "235b846a-5913-40f0-a66d-132e99290b46",
        "nummer": 1300,
        "omschrijving": "Debiteuren",
        "soort": "Verkoop",
        "nonactief": False,
    },
    {
        "id": "ba460b75-eaee-4971-aab1-98ebace25207",
        "nummer": 1100,
        "omschrijving": "Rekening-courant bank",
        "soort": "Bank",
        "nonactief": False,
    },
]

#: ``landcodeISO`` is three letters and ``landcode`` is two — a real trap, since matching the
#: wrong one silently finds nothing on an endpoint that also ignores ``$filter``.
LANDEN: list[dict[str, Any]] = [
    {
        "id": "c4335f00-c6a6-48fd-b89f-108657d12ccf",
        "naam": "Nederland",
        "landcode": "NL",
        "landcodeISO": "NLD",
    },
    {
        "id": "5f4e6caf-5520-41af-b7e5-6ee884819be0",
        "naam": "België",
        "landcode": "BE",
        "landcodeISO": "BEL",
    },
]

OMZETGROEPEN: list[dict[str, Any]] = [
    {
        "id": "94d9cbef-ec40-4e53-af50-a5f1da7b94db",
        "nummer": 5,
        "omschrijving": "Hoog btw (diensten)",
        "verkoopNederlandBtwSoort": "Hoog",
        "verkoopGrootboekNederlandIdentifier": {
            "id": "7a7b3888-7620-47c0-b248-98424219771c"
        },
    },
    {
        "id": "8315cd15-5762-4374-aae0-4bbf374e0422",
        "nummer": 1,
        "omschrijving": "Hoog btw (goederen)",
        "verkoopNederlandBtwSoort": "Hoog",
        "verkoopGrootboekNederlandIdentifier": {
            "id": "407b2467-c9b8-431e-98e4-6561bb1af549"
        },
    },
]

COMPANY_INFO: dict[str, Any] = {
    "administratieIdentifier": "37d87f31-d8f2-4dad-93f0-c2387c9ef769",
    "administratieNaam": "Testadministratie",
    "bedrijfsnaam": "Testbureau bv",
    "adres": "Dorpsstraat 1",
    "postcode": "1234 AB",
    "plaats": "Alkmaar",
    "btwNummer": "NL123456789B01",
    "kvKNummer": "12345678",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "boekhouding@testbureau.nl",
    "huidigBoekjaar": 2026,
    "artikelcodeSoort": "Numeriek",
    "artikelcodeMaxLengte": 10,
    "volgendFactuurnummer": 10001,
}

#: The scopes the live token carries, base64'd into a JWT-shaped string by :func:`_token`.
DEFAULT_SCOPES = (
    "artikelen:read artikelen:write bankieren:read bankieren:write boekhouden:read "
    "boekhouden:write documenten:read documenten:write kas:read kas:write memoriaal:read "
    "memoriaal:write orders:read orders:write rapportage relaties:read relaties:write "
    "settings:read settings:write"
)


def _token(scopes: str = DEFAULT_SCOPES) -> str:
    """A JWT-shaped token whose payload really carries the scopes claim.

    Shaped rather than signed: ``_scopes_of`` decodes without verifying on purpose (we are not
    authenticating this token — SnelStart is, on every call — we are reading what it says it may
    do), so a fake that returned an opaque string would leave that path untested and the
    "missing scopes" warning permanently blank.
    """
    import base64

    def part(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part({'scopes': scopes})}.sig"


class FakeSnelstart:
    """A SnelStart administration that holds state and answers like the live one."""

    def __init__(self, *, scopes: str = DEFAULT_SCOPES) -> None:
        self.scopes = scopes
        self.relaties: dict[str, dict[str, Any]] = {}
        self.boekingen: dict[str, dict[str, Any]] = {}
        self.facturen: dict[str, dict[str, Any]] = {}
        self.artikelen: dict[str, dict[str, Any]] = {}
        self.documenten: dict[str, dict[str, Any]] = {}
        self.company_info: dict[str, Any] = dict(COMPANY_INFO)
        #: ``(method, path, query, body)`` — never a header, never the token body.
        self.calls: list[tuple[str, str, str, Any]] = []
        #: ``path fragment -> (status, body)``. One scripted failure, cleared when it fires, so
        #: a test says "the *next* call to X fails" rather than "X is broken for ever".
        self.fail_next: dict[str, tuple[int, Any]] = {}
        #: Refuse every token mint — a rejected koppelsleutel.
        self.reject_key = False
        #: Refuse every API call at the gateway — a rejected subscription key, which is a
        #: *different* fault with a different owner and its own error path.
        self.reject_subscription = False
        #: Answer nothing at all, to exercise the unknown-write path.
        self.offline = False
        self.token_calls = 0

    # --- seeding ------------------------------------------------------------ #
    def add_relatie(self, **fields: Any) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "relatiesoort": ["Klant"],
            "naam": "Klant",
            # Allocated the way SnelStart allocates: the next code nothing holds. A fake that
            # handed out `1000 + len(rows)` would re-collide on the row created *because* of a
            # collision, which is exactly the path being tested.
            "relatiecode": self._next_relatiecode(),
            "vestigingsAdres": {"land": {"id": LANDEN[0]["id"]}},
            "correspondentieAdres": None,
            "email": None,
            "btwNummer": None,
            "kvkNummer": None,
            "memo": None,
            "modifiedOn": _now(),
            **_normalise_money(fields),
        }
        row["uri"] = f"/relaties/{row['id']}"
        self.relaties[row["id"]] = row
        return row

    def _next_relatiecode(self) -> int:
        taken = {
            row.get("relatiecode")
            for row in self.relaties.values()
            if isinstance(row.get("relatiecode"), int)
        }
        code = 1000
        while code in taken:
            code += 1
        return code

    def add_artikel(self, **fields: Any) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "artikelcode": "1",
            "omschrijving": "Artikel",
            "artikelOmzetgroep": {"id": OMZETGROEPEN[0]["id"]},
            "verkoopprijs": 0.0,
            "isNonActief": False,
            "modifiedOn": _now(),
            **_normalise_money(fields),
        }
        self.artikelen[row["id"]] = row
        return row

    def pay(self, factuurnummer: str, amount: float | None = None) -> None:
        """Record money against an invoice, the way a bank statement match would.

        The only way a test can make an invoice paid — there is deliberately no way to hand a
        status to anything. Settlement is a *fact about the administration*, and reconciliation
        must read it rather than be told it, which is the property the whole payment sync rests
        on.
        """
        for row in self.facturen.values():
            if row["factuurnummer"] == factuurnummer:
                paid = row["factuurBedrag"] if amount is None else amount
                row["openstaandSaldo"] = round(row["openstaandSaldo"] - paid, 2)
                row["modifiedOn"] = _now()
                return
        raise AssertionError(f"no invoice {factuurnummer!r} in the fake administration")

    # --- transport ---------------------------------------------------------- #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("fake snelstart is offline", request=request)

        path = request.url.path
        if path.endswith(TOKEN_PATH):
            return self._token_response(request)

        # Everything else is the API. Strip the version prefix the client's base_url adds.
        resource = path[len(PREFIX) :].strip("/") if path.startswith(PREFIX) else path.strip("/")
        query = request.url.query.decode() if request.url.query else ""
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except (json.JSONDecodeError, ValueError):
                body = None
        # Never the headers: the bearer token and the subscription key both live there.
        self.calls.append((request.method, resource, query, body))

        if self.reject_subscription:
            # Azure API Management's own wording, which is what the client's subscription-vs-
            # koppelsleutel split keys off. Getting this string wrong here would make the test
            # for that split pass for the wrong reason.
            return _json(
                401,
                {
                    "statusCode": 401,
                    "message": (
                        "Access denied due to invalid subscription key. Make sure to provide a "
                        "valid key for an active subscription."
                    ),
                },
            )

        for fragment, (status, payload) in list(self.fail_next.items()):
            if fragment in resource:
                del self.fail_next[fragment]
                return _json(status, payload)

        head = resource.split("/")[0]
        handler = getattr(self, f"_{head}", None)
        if handler is None:
            return _json(404, [{"errorCode": "ALG-0102", "message": "Onbekende fout"}])
        return handler(request, resource, body)

    def _token_response(self, request: httpx.Request) -> httpx.Response:
        self.token_calls += 1
        content = request.content.decode()
        if self.reject_key or "clientkey=" not in content:
            return _json(
                400,
                {
                    "error": (
                        "GrantType clientkey was found. Parameter clientkey does not "
                        "contain a valid value"
                    )
                },
            )
        return _json(
            200,
            {"access_token": _token(self.scopes), "token_type": "bearer", "expires_in": 3599},
        )

    # --- resources ---------------------------------------------------------- #
    def _companyInfo(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:  # noqa: N802
        return _json(200, self.company_info)

    def _btwtarieven(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:
        return _json(200, BTW_TARIEVEN)

    def _grootboeken(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:
        return self._collection(request, GROOTBOEKEN, "grootboeken")

    def _dagboeken(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:
        return self._collection(request, DAGBOEKEN, "dagboeken")

    def _kostenplaatsen(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:
        return self._collection(request, [], "kostenplaatsen")

    def _landen(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:
        return self._collection(request, LANDEN, "landen")

    def _artikelomzetgroepen(
        self, request: httpx.Request, resource: str, body: Any
    ) -> httpx.Response:
        return self._collection(request, OMZETGROEPEN, "artikelomzetgroepen")

    def _relaties(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:
        parts = resource.split("/")
        if len(parts) > 1:
            row = self.relaties.get(parts[1])
            if row is None:
                return _json(404, [{"errorCode": "REL-0005", "message": "Relatie niet gevonden"}])
            if request.method == "PUT":
                row.update(
                    {k: v for k, v in _normalise_money(body or {}).items() if k != "id"}
                )
                row["modifiedOn"] = _now()
                return _json(200, row)
            if request.method == "DELETE":
                del self.relaties[parts[1]]
                return _json(200, {})
            return _json(200, row)

        if request.method == "POST":
            payload = dict(body or {})
            name = str(payload.get("naam") or "").strip()
            if not name:
                return _json(400, [{"errorCode": "REL-0007", "message": "Naam moet gevuld zijn"}])
            if len(name) > 50:
                return _json(400, [{"errorCode": "REL-0007", "message": "Naam is te lang"}])
            code = payload.get("relatiecode")
            if code is not None and any(
                row.get("relatiecode") == code for row in self.relaties.values()
            ):
                return _json(
                    400, [{"errorCode": "REL-0008", "message": "Relatiecode reeds in gebruik"}]
                )
            return _json(201, self.add_relatie(**payload))

        return self._collection(request, list(self.relaties.values()), "relaties")

    def _artikelen(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:
        parts = resource.split("/")
        if len(parts) > 1:
            row = self.artikelen.get(parts[1])
            if row is None:
                return _json(404, [{"errorCode": "ART-0001", "message": "Artikel bestaat niet."}])
            if request.method == "PUT":
                row.update(
                    {k: v for k, v in _normalise_money(body or {}).items() if k != "id"}
                )
                row["modifiedOn"] = _now()
                return _json(200, row)
            return _json(200, row)
        if request.method == "POST":
            payload = dict(body or {})
            code = str(payload.get("artikelcode") or "")
            if not code:
                return _json(
                    400, [{"errorCode": "ART-0002", "message": "Artikelcode is verplicht."}]
                )
            if any(row["artikelcode"] == code for row in self.artikelen.values()):
                return _json(
                    400, [{"errorCode": "ART-0005", "message": "Artikelcode bestaat al."}]
                )
            return _json(201, self.add_artikel(**payload))
        return self._collection(request, list(self.artikelen.values()), "artikelen")

    def _verkoopboekingen(
        self, request: httpx.Request, resource: str, body: Any
    ) -> httpx.Response:
        parts = resource.split("/")
        if len(parts) > 1:
            row = self.boekingen.get(parts[1])
            if row is None:
                return _json(404, [{"errorCode": "BOE-0001", "message": "Niet gevonden"}])
            if request.method == "PUT":
                row.update(
                    {k: v for k, v in _normalise_money(body or {}).items() if k != "id"}
                )
                row["modifiedOn"] = _now()
                self._refresh_factuur(row)
                return _json(200, row)
            if request.method == "DELETE":
                del self.boekingen[parts[1]]
                return _json(200, {})
            return _json(200, row)

        if request.method != "POST":
            return _json(405, [{"errorCode": "ALG-0102", "message": "Onbekende fout"}])

        payload = dict(body or {})
        number = str(payload.get("factuurnummer") or "")
        if not number or len(number) > 25:
            return _json(
                400,
                [{"errorCode": "BOE-0058", "message": "Het factuurnummer is verplicht"}],
            )
        # **The behaviour this whole fake exists for.** The live API refuses a duplicate
        # number, and that refusal is how the integration answers "is it already there?".
        if any(row["factuurnummer"] == number for row in self.boekingen.values()):
            return _json(
                400, [{"errorCode": "BOE-0021", "message": "Het factuurnummer bestaat al"}]
            )
        if not payload.get("boekingsregels"):
            return _json(
                400, [{"errorCode": "BOE-0023", "message": "Er zijn geen boekingsregels"}]
            )
        klant = (payload.get("klant") or {}).get("id")
        if klant not in self.relaties:
            return _json(
                400, [{"errorCode": "BOE-0060", "message": "De opgegeven relatie is geen klant"}]
            )

        row = {
            **_normalise_money(payload),
            "id": str(uuid.uuid4()),
            "modifiedOn": _now(),
            "documents": [],
        }
        row["uri"] = f"/verkoopboekingen/{row['id']}"
        self.boekingen[row["id"]] = row
        self._refresh_factuur(row)
        return _json(201, row)

    def _refresh_factuur(self, boeking: dict[str, Any]) -> None:
        """Every boeking produces a verkoopfactuur — the live API's own derivation.

        Reproduced rather than stubbed because the payment sync reads *that* view and not the
        boeking, and a fake where the two did not track each other would make the reconciliation
        test meaningless.
        """
        from datetime import timedelta

        for row in self.facturen.values():
            if row["verkoopBoeking"]["id"] == boeking["id"]:
                row["factuurBedrag"] = _money(boeking.get("factuurbedrag") or 0)
                row["modifiedOn"] = _now()
                return
        issue = str(boeking.get("factuurdatum") or _now())[:10]
        try:
            due = datetime.fromisoformat(issue) + timedelta(
                days=int(boeking.get("betalingstermijn") or 14)
            )
        except ValueError:
            due = datetime.now(UTC)
        amount = _money(boeking.get("factuurbedrag") or 0)
        factuur = {
            "id": str(uuid.uuid4()),
            "verkoopBoeking": {"id": boeking["id"]},
            "factuurnummer": boeking["factuurnummer"],
            "factuurBedrag": amount,
            "openstaandSaldo": amount,
            "factuurDatum": f"{issue}T00:00:00",
            "vervalDatum": due.strftime("%Y-%m-%dT00:00:00"),
            "relatie": boeking.get("klant"),
            "verkoopOrders": [],
            "modifiedOn": _now(),
        }
        self.facturen[factuur["id"]] = factuur

    def _verkoopfacturen(
        self, request: httpx.Request, resource: str, body: Any
    ) -> httpx.Response:
        parts = resource.split("/")
        if len(parts) > 1:
            row = self.facturen.get(parts[1])
            return _json(200, row) if row else _json(404, [{"errorCode": "BOE-0001"}])
        return self._collection(request, list(self.facturen.values()), "verkoopfacturen")

    def _documenten(self, request: httpx.Request, resource: str, body: Any) -> httpx.Response:
        parts = resource.split("/")
        if request.method == "POST" and len(parts) > 1:
            payload = dict(body or {})
            parent = payload.get("parentIdentifier")
            if parent not in self.boekingen:
                return _json(404, [{"errorCode": "BLG-0005", "message": "Niet gevonden."}])
            content = payload.get("content") or ""
            if not content:
                return _json(400, [{"errorCode": "BLG-0011", "message": "Content is leeg."}])
            doc = {
                "id": str(uuid.uuid4()),
                "parentIdentifier": parent,
                "fileName": payload.get("fileName") or "",
                "readOnly": False,
            }
            self.documenten[doc["id"]] = doc
            self.boekingen[parent].setdefault("documents", []).append(doc)
            return _json(201, {"id": doc["id"], "uri": f"/documenten/{doc['id']}"})
        return _json(404, [{"errorCode": "BLG-0001", "message": "Bijlage niet gevonden."}])

    # --- the OData behaviour that is not in the documentation ---------------- #
    def _collection(
        self, request: httpx.Request, rows: list[dict[str, Any]], resource: str
    ) -> httpx.Response:
        """Page and (sometimes) filter, exactly as the live API does.

        ``$filter`` is applied only for endpoints in :data:`FILTERING`; for the rest it is read
        and thrown away, which is the live behaviour and the reason the client re-checks every
        predicate locally. Nothing here ever returns paging metadata, because the real API sends
        none.
        """
        params = request.url.params
        expression = params.get("$filter")
        if expression and resource in FILTERING:
            if not _known_property(expression):
                return _json(
                    400,
                    {
                        "Message": (
                            "Could not find a property named 'Nonsense' on type "
                            "'SnelStart.B2B.Api.V2.Models.Relaties.RelatieModel'."
                        )
                    },
                )
            rows = [row for row in rows if _matches(row, expression)]

        skip = int(params.get("$skip") or 0)
        top = min(int(params.get("$top") or 500), 500)
        return _json(200, rows[skip : skip + top])


def _known_property(expression: str) -> bool:
    """Would the live server recognise the property this filter names?

    Crude on purpose: the only thing being reproduced is *that an unknown property is a 400*,
    which is what stops a typo in a filter from silently returning the whole table.
    """
    return "Nonsense" not in expression


def _matches(row: dict[str, Any], expression: str) -> bool:
    """The handful of ``$filter`` shapes this integration actually sends."""
    if "Relatiesoort/any(r:r eq 'Klant')" in expression:
        return "Klant" in (row.get("relatiesoort") or [])
    if "Relatiesoort/any(r:r eq 'Eigen')" in expression:
        return "Eigen" in (row.get("relatiesoort") or [])
    for field, key in (
        ("Factuurnummer", "factuurnummer"),
        ("Artikelcode", "artikelcode"),
        ("Naam", "naam"),
        ("Grootboekfunctie", "grootboekfunctie"),
    ):
        prefix = f"{field} eq '"
        if expression.startswith(prefix):
            wanted = expression[len(prefix) : expression.rindex("'")].replace("''", "'")
            return str(row.get(key) or "") == wanted
    return True


def _json(status: int, payload: Any) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _money(value: Any) -> Any:
    """A money field as the live API stores it: a number, whatever shape it arrived in.

    SnelStart accepts ``"121.00"`` and answers ``121.00`` — the .NET deserialiser parses a JSON
    string into a ``decimal`` and re-serialises it as a number. Reproduced rather than passed
    through, so a test asserting on the stored shape is asserting on the real one.
    """
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _normalise_money(payload: Any) -> Any:
    """Walk a payload and normalise every field SnelStart treats as money."""
    fields = {
        "bedrag", "factuurbedrag", "btwBedrag", "verkoopprijs", "inkoopprijs",
        "openstaandSaldo", "factuurBedrag", "kredietLimiet", "stuksprijs", "totaal",
    }
    if isinstance(payload, dict):
        return {
            key: _money(value) if key in fields else _normalise_money(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_normalise_money(item) for item in payload]
    return payload


def _now() -> str:
    """A SnelStart timestamp: naive local wall clock, no zone. Exactly what the live API sends."""
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="milliseconds")
