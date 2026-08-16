#!/usr/bin/env python3
"""Fill a *fresh* schakl instance with a fictional agency's book of business.

Why this exists: the manual on schakl.dev is illustrated with screenshots of the real
application, and the only two other places to take them from are both wrong. The dev stack
holds a real agency's clients, revenue and mailboxes — none of which may appear on a public
website — and an empty instance shows nothing but "Geen resultaten". So the screenshots come
from a throwaway instance seeded by this script: every name, address and amount below is
invented, and every one of them is written through the ordinary REST API, so what a reader
sees on a screenshot is a screen the service layer actually produced.

It is deliberately API-driven rather than SQL. An INSERT would be faster and would skip
validation, the activity trail, the events other modules subscribe to and the per-tenant
custom-field rules — which is to say it would produce rows the application never would, and
the first screenshot of a broken state would be the one nobody noticed.

Usage (against an instance you are happy to overwrite — never the dev stack):

    python3 scripts/seed-demo.py --api http://127.0.0.1:8411 --host localhost \\
        --email sanne@example.com --password 'DemoDocs!2026'

Stdlib only, so it runs with a bare `python3` and no virtualenv.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from typing import Any

# One seed, so a re-run of the whole script produces the same agency. Screenshots taken a week
# apart otherwise disagree about numbers the prose around them quotes.
random.seed(20260816)

TODAY = date.today()


class Api:
    """The thinnest possible session: a cookie jar and an error that says what went wrong."""

    def __init__(self, base: str, host: str) -> None:
        self.base = base.rstrip("/")
        self.host = host
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.created: dict[str, int] = {}
        self.skipped: dict[str, int] = {}
        self.skipped_detail: dict[str, str] = {}

    def _request(
        self, method: str, path: str, body: Any = None, form: str | None = None
    ) -> tuple[int, Any]:
        url = f"{self.base}{path}"
        data = None
        headers = {"Host": self.host, "Accept": "application/json"}
        if form is not None:
            data = form.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=120) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except Exception:
                return exc.code, raw.decode(errors="replace")

    def login(self, email: str, password: str) -> None:
        form = urllib.parse.urlencode({"username": email, "password": password})
        status, body = self._request("POST", "/api/v1/auth/login", form=form)
        if status not in (200, 204):
            raise SystemExit(f"login failed ({status}): {body}")

    def get(self, path: str) -> Any:
        status, body = self._request("GET", path)
        if status >= 400:
            raise SystemExit(f"GET {path} -> {status}: {body}")
        return body

    def post(self, path: str, body: Any, *, tolerate: tuple[int, ...] = ()) -> Any:
        status, out = self._request("POST", path, body)
        if status >= 400:
            if status in tolerate:
                self._tolerated(path, status, out)
                return None
            raise SystemExit(
                f"POST {path} -> {status}: {json.dumps(out)[:600]}\n{json.dumps(body)[:600]}"
            )
        self.created[path.split("?")[0]] = self.created.get(path.split("?")[0], 0) + 1
        return out

    def _tolerated(self, path: str, status: int, out: Any) -> None:
        """Record a refusal we chose to carry on past, and print it at the end.

        `tolerate` exists so a re-run does not die on the rows it already made, and it hid a
        real bug: every leave request went in with `{"decision": "approved"}` where the schema
        wants `{"approved": true}`, so all four 422'd, all four were swallowed, and the demo
        instance quietly had no approved leave at all. A tolerated failure is still a failure —
        it just is not fatal — so it has to leave a mark somewhere a person will look.
        """
        key = f"{status} {path.split('?')[0]}"
        self.skipped[key] = self.skipped.get(key, 0) + 1
        if key not in self.skipped_detail:
            self.skipped_detail[key] = json.dumps(out)[:200]

    def upload(self, path: str, filename: str, blob: bytes, fields: dict[str, Any]) -> Any:
        """A multipart POST, for the one endpoint that takes a file.

        `email` is a protected interaction kind (#262): the ordinary create refuses it, because
        an e-mail is something that *arrived* rather than something somebody typed. So the demo
        mail is built as a real RFC 5322 message and uploaded the way a person forwarding one
        from their desktop client would — which is also the only honest way to produce the
        screenshot of a mail on a client's timeline.
        """
        boundary = "----schakldemo7c2f19"
        parts: list[bytes] = []
        for key, value in fields.items():
            if value is None:
                continue
            for item in value if isinstance(value, list) else [value]:
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{item}\r\n'.encode()
                )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: message/rfc822\r\n\r\n".encode()
        )
        parts.append(blob + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=body,
            method="POST",
            headers={
                "Host": self.host,
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with self.opener.open(req, timeout=120) as resp:
                raw = resp.read()
                self.created[path] = self.created.get(path, 0) + 1
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError:
            return None

    def patch(self, path: str, body: Any, *, tolerate: tuple[int, ...] = ()) -> Any:
        status, out = self._request("PATCH", path, body)
        if status >= 400:
            if status not in tolerate:
                raise SystemExit(f"PATCH {path} -> {status}: {json.dumps(out)[:600]}")
            self._tolerated(path, status, out)
        return out

    def put(self, path: str, body: Any, *, tolerate: tuple[int, ...] = ()) -> Any:
        status, out = self._request("PUT", path, body)
        if status >= 400 and status not in tolerate:
            raise SystemExit(f"PUT {path} -> {status}: {json.dumps(out)[:600]}")
        return out


import urllib.parse  # noqa: E402  (after the class, so the module docstring stays first)


def iso(d: date) -> str:
    return d.isoformat()


def build_eml(*, subject: str, sender: str, to: str, when: date, body: str) -> bytes:
    """A minimal but genuine RFC 5322 message, so the ingest path parses a real thing."""
    from email.message import EmailMessage
    from email.utils import format_datetime, make_msgid

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = format_datetime(
        datetime(when.year, when.month, when.day, 10, 24, tzinfo=timezone.utc)
    )
    msg["Message-ID"] = make_msgid(domain="example")
    msg.set_content(body)
    return msg.as_bytes()


def at(d: date, hour: int, minute: int = 0) -> str:
    """A wall-clock moment in the org's zone, expressed as the UTC instant the API stores.

    The demo org runs Europe/Amsterdam and every date here is inside CEST, so the offset is a
    constant two hours. Hard-coding that is fine *for generated sample data* and would not be
    fine in the application (CLAUDE.md §8) — the point of a timestamp here is that the week
    grid looks like a working week, not that it survives a DST boundary.
    """
    return (
        datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc) - timedelta(hours=2)
    ).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------------------
# The agency
# --------------------------------------------------------------------------------------

TEAM = [
    # (full name, email, role) — the owner already exists from the setup wizard.
    ("Thomas Bakker", "thomas@example.com", "member"),
    ("Lisa Vermeer", "lisa@example.com", "member"),
    ("Youssef El Amrani", "youssef@example.com", "admin"),
    ("Marit Jansen", "marit@example.com", "member"),
    ("Daan Willems", "daan@example.com", "member"),
]

# Fictional Dutch small businesses. Addresses are real streets in the wrong towns and the
# postcodes are invented, so nothing here resolves to a real company.
CLIENTS = [
    {
        "name": "Bakkerij Van Loon",
        "status": "active",
        "city": "Utrecht",
        "address_line1": "Nachtegaalstraat",
        "house_number": "112",
        "postal_code": "3581 AK",
        "phone": "+31 30 233 4410",
        "website": "https://bakkerijvanloon.example",
        "vat_number": "NL812345678B01",
        "coc_number": "30112233",
        "invoice_email": "administratie@bakkerijvanloon.example",
        "notes": "Vier filialen. Bestelmodule draait op WooCommerce; kerst- en paasdrukte "
        "vraagt elk jaar om extra capaciteit op de hosting.",
    },
    {
        "name": "Fysiotherapie De Kade",
        "status": "active",
        "city": "Zwolle",
        "address_line1": "Thorbeckegracht",
        "house_number": "27",
        "postal_code": "8011 VN",
        "phone": "+31 38 421 7788",
        "website": "https://fysiodekade.example",
        "vat_number": "NL823456789B01",
        "coc_number": "05442211",
        "invoice_email": "info@fysiodekade.example",
        "notes": "Afsprakenmodule koppelt met hun praktijksoftware. Contactpersoon is de "
        "praktijkhouder, facturen gaan naar het boekhoudkantoor.",
    },
    {
        "name": "Groenhuis Interieur",
        "status": "active",
        "city": "Eindhoven",
        "address_line1": "Kleine Berg",
        "house_number": "48",
        "postal_code": "5611 JV",
        "phone": "+31 40 296 1120",
        "website": "https://groenhuis-interieur.example",
        "vat_number": "NL834567890B01",
        "coc_number": "17223344",
        "invoice_email": "facturen@groenhuis-interieur.example",
        "notes": "Showroom plus webshop. Doet zelf de content, wij het onderhoud en de SEO.",
    },
    {
        "name": "Van Dijk & Partners Advocaten",
        "status": "active",
        "city": "Den Haag",
        "address_line1": "Javastraat",
        "house_number": "9",
        "postal_code": "2585 AA",
        "phone": "+31 70 361 5522",
        "website": "https://vandijkpartners.example",
        "vat_number": "NL845678901B01",
        "coc_number": "27334455",
        "invoice_email": "crediteuren@vandijkpartners.example",
        "notes": "Streng op privacy: geen trackingpixels, cookiebanner met opt-in, "
        "analytics zonder persoonsgegevens.",
    },
    {
        "name": "Nova Fietsen",
        "status": "active",
        "city": "Groningen",
        "address_line1": "Oude Boteringestraat",
        "house_number": "63",
        "postal_code": "9712 GK",
        "phone": "+31 50 313 8890",
        "website": "https://novafietsen.example",
        "vat_number": "NL856789012B01",
        "coc_number": "02445566",
        "invoice_email": "administratie@novafietsen.example",
        "notes": "Twee vestigingen, groeiende webshop. Adverteert het hele seizoen op "
        "Google Ads; wij beheren het budget.",
    },
    {
        "name": "Camping De Waterlinie",
        "status": "active",
        "city": "Culemborg",
        "address_line1": "Veerweg",
        "house_number": "3",
        "postal_code": "4101 AB",
        "phone": "+31 345 512 200",
        "website": "https://campingdewaterlinie.example",
        "vat_number": "NL867890123B01",
        "coc_number": "11556677",
        "invoice_email": "info@campingdewaterlinie.example",
        "notes": "Sterk seizoensgebonden: juli en augustus zijn het hele jaar. "
        "Rapportages vergelijken daarom met vorig jaar, niet met vorige maand.",
    },
    {
        "name": "Kliniek Zonneveld",
        "status": "active",
        "city": "Haarlem",
        "address_line1": "Gedempte Oude Gracht",
        "house_number": "88",
        "postal_code": "2011 GR",
        "phone": "+31 23 531 4477",
        "website": "https://kliniekzonneveld.example",
        "vat_number": "NL878901234B01",
        "coc_number": "34667788",
        "invoice_email": "facturatie@kliniekzonneveld.example",
        "notes": "Meerdere behandelpagina's met eigen zoekwoorden. Maandelijkse rapportage "
        "gaat naar de directie.",
    },
    {
        "name": "Meijer Installatietechniek",
        "status": "active",
        "city": "Apeldoorn",
        "address_line1": "Deventerstraat",
        "house_number": "201",
        "postal_code": "7321 CD",
        "phone": "+31 55 522 9010",
        "website": "https://meijer-installatie.example",
        "vat_number": "NL889012345B01",
        "coc_number": "08778899",
        "invoice_email": "administratie@meijer-installatie.example",
        "notes": "Offerteaanvragen zijn de enige conversie die telt. Formulier is gekoppeld "
        "als conversie in Tag Manager.",
    },
    {
        "name": "Restaurant De Vlinder",
        "status": "onboarding",
        "city": "Maastricht",
        "address_line1": "Sint Bernardusstraat",
        "house_number": "14",
        "postal_code": "6211 HL",
        "phone": "+31 43 325 6612",
        "website": "https://restaurantdevlinder.example",
        "invoice_email": "info@restaurantdevlinder.example",
        "notes": "Nieuwe klant. Site staat nog bij de vorige bouwer; verhuizing gepland.",
    },
    {
        "name": "Bouwbedrijf Merelaan",
        "status": "active",
        "city": "Alkmaar",
        "address_line1": "Kanaalkade",
        "house_number": "42",
        "postal_code": "1811 LP",
        "phone": "+31 72 511 3320",
        "website": "https://merelaan-bouw.example",
        "vat_number": "NL890123456B01",
        "coc_number": "37889900",
        "invoice_email": "boekhouding@merelaan-bouw.example",
        "notes": "Projectenportfolio met veel beeld. Hosting is zwaarder dan gemiddeld.",
    },
    {
        "name": "Atelier Roosmarijn",
        "status": "lead",
        "city": "Deventer",
        "address_line1": "Brink",
        "house_number": "76",
        "postal_code": "7411 BW",
        "phone": "+31 570 612 845",
        "notes": "Aangedragen door Groenhuis Interieur. Wil een portfoliosite en een "
        "kleine webshop. Offerte staat open.",
    },
    {
        "name": "Praktijk Sterrenlaan",
        "status": "offboarding",
        "city": "Amersfoort",
        "address_line1": "Sterrenlaan",
        "house_number": "5",
        "postal_code": "3813 VA",
        "phone": "+31 33 461 2200",
        "notes": "Praktijk wordt overgenomen; de nieuwe eigenaar neemt zijn eigen bureau mee. "
        "Domein en hosting lopen tot het einde van het jaar door.",
    },
    {
        "name": "Oud & Nieuw Antiek",
        "status": "archived",
        "city": "Dordrecht",
        "address_line1": "Voorstraat",
        "house_number": "310",
        "postal_code": "3311 ET",
        "notes": "Gestopt in 2024. Bewaard voor de historie; domein is overgedragen.",
    },
]

CONTACTS = {
    "Bakkerij Van Loon": [
        ("Marieke", "van Loon", "marieke@bakkerijvanloon.example", "Eigenaar", "+31 6 2214 8890"),
        ("Peter", "de Wit", "peter@bakkerijvanloon.example", "Bedrijfsleider", "+31 6 1180 3345"),
    ],
    "Fysiotherapie De Kade": [
        ("Joost", "Hendriks", "joost@fysiodekade.example", "Praktijkhouder", "+31 6 4471 2210"),
    ],
    "Groenhuis Interieur": [
        ("Ilse", "Groenhuis", "ilse@groenhuis-interieur.example", "Eigenaar", "+31 6 3320 7745"),
        ("Bram", "Kuipers", "bram@groenhuis-interieur.example", "Marketing", "+31 6 2298 1102"),
    ],
    "Van Dijk & Partners Advocaten": [
        ("Annelies", "van Dijk", "a.vandijk@vandijkpartners.example", "Partner", "+31 6 5512 3390"),
        ("Rachid", "Bouzid", "r.bouzid@vandijkpartners.example", "Officemanager", "+31 6 1123 8876"),
    ],
    "Nova Fietsen": [
        ("Sander", "Nauta", "sander@novafietsen.example", "Eigenaar", "+31 6 2765 4412"),
        ("Fenna", "de Boer", "fenna@novafietsen.example", "E-commerce", "+31 6 3388 2201"),
    ],
    "Camping De Waterlinie": [
        ("Hans", "Verkerk", "hans@campingdewaterlinie.example", "Eigenaar", "+31 6 1902 7734"),
    ],
    "Kliniek Zonneveld": [
        ("Dr. Nadia", "Farahani", "n.farahani@kliniekzonneveld.example", "Directie", "+31 6 4408 9912"),
        ("Wouter", "Smits", "w.smits@kliniekzonneveld.example", "Communicatie", "+31 6 2231 6650"),
    ],
    "Meijer Installatietechniek": [
        ("Gerard", "Meijer", "gerard@meijer-installatie.example", "Directeur", "+31 6 5140 3328"),
    ],
    "Restaurant De Vlinder": [
        ("Chantal", "Ruiters", "chantal@restaurantdevlinder.example", "Eigenaar", "+31 6 2874 1163"),
    ],
    "Bouwbedrijf Merelaan": [
        ("Rob", "Merelaan", "rob@merelaan-bouw.example", "Directeur", "+31 6 3061 9985"),
        ("Kim", "Doornbos", "kim@merelaan-bouw.example", "Projectleider", "+31 6 4423 1170"),
    ],
    "Atelier Roosmarijn": [
        ("Roosmarijn", "Bakhuis", "roos@atelierroosmarijn.example", "Eigenaar", "+31 6 1755 2043"),
    ],
    "Praktijk Sterrenlaan": [
        ("Ineke", "Sterrenberg", "ineke@praktijksterrenlaan.example", "Praktijkhouder", "+31 6 2210 8837"),
    ],
}

DOMAINS = {
    "bakkerijvanloon.example": ("Bakkerij Van Loon", 14.50, 8),
    "vanloonbrood.example": ("Bakkerij Van Loon", 14.50, 3),
    "fysiodekade.example": ("Fysiotherapie De Kade", 14.50, 11),
    "groenhuis-interieur.example": ("Groenhuis Interieur", 14.50, 5),
    "groenhuis.example": ("Groenhuis Interieur", 21.00, 5),
    "vandijkpartners.example": ("Van Dijk & Partners Advocaten", 14.50, 2),
    "novafietsen.example": ("Nova Fietsen", 14.50, 9),
    "novafietsen-shop.example": ("Nova Fietsen", 21.00, 9),
    "campingdewaterlinie.example": ("Camping De Waterlinie", 14.50, 1),
    "kliniekzonneveld.example": ("Kliniek Zonneveld", 14.50, 6),
    "meijer-installatie.example": ("Meijer Installatietechniek", 14.50, 10),
    "merelaan-bouw.example": ("Bouwbedrijf Merelaan", 14.50, 4),
    "restaurantdevlinder.example": ("Restaurant De Vlinder", 14.50, 7),
    "praktijksterrenlaan.example": ("Praktijk Sterrenlaan", 14.50, 12),
}

PROJECTS = [
    # (client, name, budget hours, budget amount, status, description)
    ("Bakkerij Van Loon", "Webshop bestelmodule", 120, 9600, "active",
     "Online bestellen voor afhalen per filiaal, met tijdvakken en een dagelijkse limiet."),
    ("Nova Fietsen", "Webshop migratie", 180, 15300, "active",
     "Van de oude shop naar een nieuw platform, inclusief 1.200 producten en de redirects."),
    ("Kliniek Zonneveld", "Nieuwe behandelpagina's", 64, 5440, "active",
     "Acht behandelingen, elk met een eigen pagina, zoekwoorden en een aanvraagformulier."),
    ("Van Dijk & Partners Advocaten", "Privacy-audit website", 24, 2160, "active",
     "Cookiebanner, trackers en formulieren doorlopen; advies vastgelegd in één rapport."),
    ("Bouwbedrijf Merelaan", "Projectenportfolio", 90, 7200, "active",
     "Portfolio met grote beelden, gefilterd op type project en regio."),
    ("Groenhuis Interieur", "SEO-verbetertraject", 40, 3600, "active",
     "Techniek, structuur en teksten voor de twintig belangrijkste pagina's."),
    ("Camping De Waterlinie", "Boekingsflow verbeteren", 56, 4480, "on_hold",
     "Wacht op de nieuwe API van hun boekingssysteem; hervat na het seizoen."),
    ("Fysiotherapie De Kade", "Afsprakenmodule", 48, 4080, "completed",
     "Koppeling met de praktijksoftware, opgeleverd en overgedragen."),
    ("Meijer Installatietechniek", "Conversiemeting opzetten", 16, 1440, "active",
     "Offerteformulier als conversie in Tag Manager, doorgezet naar Google Ads."),
    ("Restaurant De Vlinder", "Website verhuizen", 20, 1700, "active",
     "Overname van de vorige bouwer: domein, hosting, e-mail en de site zelf."),
]

TASKS = [
    # (client, project or None, title, status, days from today for due date, minutes, description)
    ("Nova Fietsen", "Webshop migratie", "Productfeed opschonen", "in_progress", 2, 240,
     "1.200 producten: dubbele SKU's eruit, maten normaliseren, foto's op één formaat."),
    ("Nova Fietsen", "Webshop migratie", "301-redirects opstellen", "open", 5, 180,
     "Oude URL's naar nieuwe, inclusief de categoriepagina's die samengevoegd worden."),
    ("Nova Fietsen", "Webshop migratie", "Testbestelling met iDEAL", "open", 8, 60, None),
    ("Bakkerij Van Loon", "Webshop bestelmodule", "Tijdvakken per filiaal instellen", "in_progress", 1, 120,
     "Elk filiaal een eigen openingsrooster en een dagelijkse maximumcapaciteit."),
    ("Bakkerij Van Loon", "Webshop bestelmodule", "Bonprinter koppelen", "open", 6, 180, None),
    ("Bakkerij Van Loon", None, "Kerstassortiment op de site zetten", "open", 21, 90, None),
    ("Kliniek Zonneveld", "Nieuwe behandelpagina's", "Teksten laten nakijken door de directie", "open", -3, 45,
     "Vier van de acht pagina's liggen klaar. Wacht op akkoord."),
    ("Kliniek Zonneveld", "Nieuwe behandelpagina's", "Zoekwoorden per behandeling vaststellen", "done", -10, 120, None),
    ("Van Dijk & Partners Advocaten", "Privacy-audit website", "Cookiebanner opnieuw inrichten", "in_progress", 4, 150,
     "Opt-in per categorie, niets laden voor toestemming, en een logboek van de keuzes."),
    ("Van Dijk & Partners Advocaten", "Privacy-audit website", "Rapport opleveren", "open", 11, 120, None),
    ("Bouwbedrijf Merelaan", "Projectenportfolio", "Beeldmateriaal comprimeren", "open", -1, 90,
     "Ruim 300 foto's; doel is onder de 200 kB per beeld zonder zichtbaar verlies."),
    ("Bouwbedrijf Merelaan", "Projectenportfolio", "Filter op regio bouwen", "in_progress", 3, 240, None),
    ("Groenhuis Interieur", "SEO-verbetertraject", "Interne links herzien", "open", 7, 120, None),
    ("Groenhuis Interieur", None, "Maandrapportage nakijken", "open", 9, 30, None),
    ("Meijer Installatietechniek", "Conversiemeting opzetten", "Offerteformulier als conversie aanmerken", "done", -6, 60, None),
    ("Meijer Installatietechniek", "Conversiemeting opzetten", "Conversie doorzetten naar Google Ads", "in_progress", 2, 45, None),
    ("Restaurant De Vlinder", "Website verhuizen", "Nameservers verzetten", "open", 4, 30,
     "Pas doen nadat de site op de nieuwe hosting draait en de e-mail is nagelopen."),
    ("Restaurant De Vlinder", "Website verhuizen", "E-mailaccounts overzetten", "open", 3, 120, None),
    ("Fysiotherapie De Kade", None, "Jaarlijkse update van het CMS", "open", 14, 90, None),
    ("Camping De Waterlinie", None, "Seizoensrapportage samenstellen", "open", 12, 60, None),
]

CHECKLISTS = {
    "Nameservers verzetten": ("Verhuisdraaiboek", [
        "Huidige DNS-records exporteren",
        "Site op de nieuwe hosting testen via het IP-adres",
        "TTL verlagen naar 300 seconden",
        "Nameservers bij de registrar aanpassen",
        "E-mail (MX, SPF, DKIM, DMARC) natrekken",
        "TTL weer omhoog na 48 uur",
    ]),
    "Testbestelling met iDEAL": ("Acceptatie webshop", [
        "Product in de winkelwagen leggen",
        "Afrekenen met iDEAL in testmodus",
        "Bevestigingsmail controleren",
        "Bestelling in het beheer terugvinden",
    ]),
}

SUBSCRIPTIONS = [
    # (client, name, amount per period, interval, included hours, months running)
    ("Bakkerij Van Loon", "Hosting & onderhoud", 89.00, "monthly", 1, 26),
    ("Fysiotherapie De Kade", "Hosting & onderhoud", 55.00, "monthly", 0.5, 34),
    ("Groenhuis Interieur", "Hosting & onderhoud", 75.00, "monthly", 1, 19),
    ("Groenhuis Interieur", "SEO-onderhoud", 450.00, "monthly", 5, 12),
    ("Van Dijk & Partners Advocaten", "Hosting & onderhoud", 65.00, "monthly", 0.5, 41),
    ("Nova Fietsen", "Hosting & onderhoud webshop", 145.00, "monthly", 2, 22),
    ("Nova Fietsen", "Google Ads-beheer", 395.00, "monthly", 4, 15),
    ("Camping De Waterlinie", "Hosting & onderhoud", 65.00, "monthly", 0.5, 30),
    ("Kliniek Zonneveld", "Hosting & onderhoud", 75.00, "monthly", 1, 28),
    ("Kliniek Zonneveld", "Maandelijkse rapportage", 195.00, "monthly", 2, 18),
    ("Meijer Installatietechniek", "Hosting & onderhoud", 65.00, "monthly", 0.5, 24),
    ("Bouwbedrijf Merelaan", "Hosting & onderhoud", 95.00, "monthly", 1, 16),
    ("Praktijk Sterrenlaan", "Hosting & onderhoud", 55.00, "monthly", 0.5, 44),
]

PRODUCTS = [
    ("Ontwikkeluur", "UUR-DEV", 95.00, "uur", "Ontwikkeling, front- en back-end."),
    ("Ontwerpuur", "UUR-DES", 95.00, "uur", "Ontwerp, UX en beeldbewerking."),
    ("Uur online marketing", "UUR-MKT", 105.00, "uur", "SEO, advertenties en analyse."),
    ("Strategie-uur", "UUR-STR", 135.00, "uur", "Advies, workshops en begeleiding."),
    ("Hostingpakket klein", "HOST-S", 55.00, "maand", "Gedeelde hosting, dagelijkse back-up."),
    ("Hostingpakket groot", "HOST-L", 145.00, "maand", "Eigen resources, wekelijkse controle."),
    ("SSL-certificaat", "SSL", 0.00, "jaar", "Let's Encrypt, automatisch vernieuwd."),
    ("Spoedtoeslag", "SPOED", 45.00, "uur", "Buiten kantooruren of binnen 24 uur."),
]

TIME_DESCRIPTIONS = [
    "Overleg met de klant over de planning",
    "Productfeed opschonen en importeren",
    "Sjabloon van de categoriepagina afgebouwd",
    "Redirects opgesteld en getest",
    "Formulier gekoppeld en conversie ingericht",
    "Beeldmateriaal geoptimaliseerd",
    "Teksten geredigeerd en doorgevoerd",
    "Zoekwoordonderzoek uitgewerkt",
    "Maandrapportage samengesteld",
    "CMS bijgewerkt en getest",
    "Bug opgelost in de bestelmodule",
    "Advertentiegroepen herzien",
    "Nabellen over de offerte",
    "Back-up teruggezet op de testomgeving",
]

INTERACTIONS = [
    # (client, kind, subject, direction, days ago, body)
    ("Nova Fietsen", "email", "Re: planning migratie webshop", "inbound", 1,
     "Dag Sanne,\n\nDe productfeed is bijgewerkt zoals besproken. Kunnen we de livegang een week "
     "opschuiven? De leverancier levert de nieuwe fotografie pas eind volgende week aan.\n\n"
     "Groet,\nSander"),
    ("Nova Fietsen", "call", "Telefonisch over de livegang", "outbound", 1,
     "Livegang verzet naar de week erna. Sander regelt de fotografie, wij houden de redirects "
     "alvast klaar. Geen extra kosten afgesproken."),
    ("Bakkerij Van Loon", "email", "Bestelmodule: tijdvakken per filiaal", "inbound", 2,
     "Hoi,\n\nUtrecht Centrum wil op zaterdag tot 16:00 bestellingen aannemen in plaats van 14:00. "
     "Kan dat per filiaal ingesteld worden?\n\nMarieke"),
    ("Kliniek Zonneveld", "physical_meeting", "Kwartaaloverleg", "outbound", 4,
     "Doorgenomen: de acht behandelpagina's, de positie op 'huidverbetering' en het budget voor "
     "Q4. Directie wil de teksten zelf nakijken voor publicatie."),
    ("Van Dijk & Partners Advocaten", "email", "Cookiebanner: akkoord op de opzet", "inbound", 5,
     "Beste Sanne,\n\nAkkoord met de voorgestelde opzet. Belangrijk blijft dat er niets laadt "
     "voordat er toestemming is.\n\nMet vriendelijke groet,\nAnnelies van Dijk"),
    ("Groenhuis Interieur", "online_meeting", "Maandelijkse SEO-stand", "outbound", 6,
     "Posities op de twintig hoofdzoekwoorden besproken. Interne links zijn de eerstvolgende stap."),
    ("Meijer Installatietechniek", "call", "Offerteaanvragen komen niet binnen", "inbound", 7,
     "Gerard belde: sinds vrijdag geen aanvragen meer gezien. Bleek een volle mailbox te zijn, "
     "niet het formulier. Opgelost tijdens het gesprek."),
    ("Restaurant De Vlinder", "email", "Toegang tot het huidige domein", "outbound", 8,
     "Dag Chantal,\n\nOm de verhuizing te kunnen plannen hebben we de inloggegevens van de "
     "huidige registrar nodig, of een verhuistoken.\n\nGroet,\nYoussef"),
    ("Camping De Waterlinie", "email", "Seizoenscijfers juli", "outbound", 10,
     "Hoi Hans,\n\nJuli zit erop: 18% meer bezoekers dan vorig jaar juli, en de boekingsknop is "
     "vaker aangeklikt. Het volledige rapport staat klaar in het portaal.\n\nGroet,\nMarit"),
    ("Bouwbedrijf Merelaan", "physical_meeting", "Portfolio doorgenomen op kantoor", "outbound", 12,
     "Rob wil het filter op regio erbij, en de projectpagina's korter. Beeld blijft leidend."),
    ("Atelier Roosmarijn", "call", "Kennismaking", "inbound", 3,
     "Doorverwezen door Groenhuis. Wil een portfoliosite met een kleine webshop, budget rond de "
     "€ 6.000. Offerte toegezegd voor volgende week."),
    ("Fysiotherapie De Kade", "email", "Afsprakenmodule werkt naar behoren", "inbound", 15,
     "Dank, hij draait. De praktijkassistenten zijn er blij mee.\n\nJoost"),
]


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


def seed(api: Api) -> None:
    def log(msg: str) -> None:
        print(f"  {msg}", flush=True)

    # --- lookups the setup wizard already seeded -------------------------------------
    tax = {t["category"]: t["id"] for t in api.get("/api/v1/invoicing/tax-rates")}
    high = tax["standard"]
    low = tax.get("reduced", high)
    leave_types = {t["key"]: t["id"] for t in api.get("/api/v1/leave/types")}

    # --- the team --------------------------------------------------------------------
    print("Team")
    for full_name, email, role in TEAM:
        api.post(
            "/api/v1/members/invite",
            {"email": email, "full_name": full_name, "role": role, "send_email": False},
            tolerate=(409, 422),
        )
    members = api.get("/api/v1/members")
    by_email = {m["email"]: m for m in members}
    staff = [m for m in members if m["email"] != "daan@example.com"]
    log(f"{len(members)} members")

    # Employment: four employees on a 40-hour week, one four-day part-timer, one freelancer.
    # The freelance row is what makes the availability surfaces (#368) exist at all.
    week5 = {
        d: {"start": "09:00", "end": "17:30", "breaks": [{"start": "12:30", "end": "13:00"}]}
        for d in ("mon", "tue", "wed", "thu", "fri")
    }
    week4 = {d: v for d, v in week5.items() if d != "fri"}
    week3 = {d: v for d, v in week5.items() if d in ("tue", "wed", "thu")}
    contracts = [
        ("sanne@example.com", "employee", 40, week5),
        ("thomas@example.com", "employee", 40, week5),
        ("lisa@example.com", "employee", 32, week4),
        ("youssef@example.com", "employee", 40, week5),
        ("marit@example.com", "employee", 40, week5),
        ("daan@example.com", "freelance", 24, week3),
    ]
    for email, kind, hours, schedule in contracts:
        m = by_email.get(email)
        if not m:
            continue
        api.post(
            "/api/v1/leave/contracts",
            {
                "user_id": m["user_id"],
                "start_date": iso(date(TODAY.year - 1, 1, 1)),
                "employment_type": kind,
                "contract_hours_per_week": hours,
                "schedule": schedule,
            },
            tolerate=(409, 422),
        )
    log(f"{len(contracts)} employment periods")

    # --- the price list --------------------------------------------------------------
    print("Products")
    for name, code, price, unit, desc in PRODUCTS:
        api.post(
            "/api/v1/invoicing/products",
            {
                "name": name,
                "code": code,
                "unit_price": price,
                "unit": unit,
                "description": desc,
                "tax_rate_id": high,
                "active": True,
            },
            tolerate=(409, 422),
        )
    log(f"{len(PRODUCTS)} products")

    # --- clients ---------------------------------------------------------------------
    print("Clients")
    owners = [m["user_id"] for m in staff]
    companies: dict[str, str] = {}
    for i, c in enumerate(CLIENTS):
        payload = dict(c)
        payload["country"] = "NL"
        payload["responsible_user_id"] = owners[i % len(owners)]
        out = api.post("/api/v1/companies", payload, tolerate=(409, 422))
        if out:
            companies[c["name"]] = out["id"]
    if len(companies) < len(CLIENTS):  # a re-run: pick the existing ids back up
        for row in api.get("/api/v1/companies?limit=200")["items"]:
            companies[row["name"]] = row["id"]
    log(f"{len(companies)} clients")

    print("Contacts")
    contacts: dict[str, str] = {}
    for company, people in CONTACTS.items():
        cid = companies.get(company)
        if not cid:
            continue
        for first, last, email, title, phone in people:
            out = api.post(
                "/api/v1/contacts",
                {
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                    "job_title": title,
                    "phone": phone,
                    "company_ids": [cid],
                },
                tolerate=(409, 422),
            )
            if out:
                contacts[email] = out["id"]
    log(f"{len(contacts)} contacts")

    # --- domains, websites, hosting --------------------------------------------------
    print("Domains, websites and hosting")
    api.post(
        "/api/v1/domains/tld-prices",
        {"tld": "example", "price": 14.50, "currency": "EUR"},
        tolerate=(409, 422),
    )
    hostings: dict[str, str] = {}
    for name, ip in (("Webcluster A (Amsterdam)", "10.20.0.11"), ("Webcluster B (Amsterdam)", "10.20.0.12")):
        out = api.post("/api/v1/hosting", {"name": name, "ip_address": ip}, tolerate=(409, 422))
        if out:
            hostings[name] = out["id"]
    cluster = list(hostings.values())
    domains: dict[str, str] = {}
    for i, (name, (client, _price, month)) in enumerate(DOMAINS.items()):
        cid = companies.get(client)
        if not cid:
            continue
        day = random.randint(1, 27)
        start = date(TODAY.year - random.randint(2, 7), month, day)
        renews_this_year = (month, day) > (TODAY.month, TODAY.day)
        out = api.post(
            "/api/v1/domains",
            {
                "name": name,
                "company_id": cid,
                "status": "active",
                "start_date": iso(start),
                "next_invoice_date": iso(date(TODAY.year + (0 if renews_this_year else 1), month, day)),
                "email_enabled": True,
            },
            tolerate=(409, 422),
        )
        if out:
            domains[name] = out["id"]
            api.post(
                "/api/v1/websites",
                {
                    "domain_id": out["id"],
                    "root": True,
                    "hosting_id": cluster[i % len(cluster)] if cluster else None,
                },
                tolerate=(409, 422),
            )
    log(f"{len(domains)} domains, {len(hostings)} hosting accounts")

    # --- subscriptions ---------------------------------------------------------------
    print("Subscriptions")
    subs = 0
    for client, name, amount, interval, hours, months in SUBSCRIPTIONS:
        cid = companies.get(client)
        if not cid:
            continue
        start = (TODAY.replace(day=1) - timedelta(days=months * 30)).replace(day=1)
        out = api.post(
            "/api/v1/subscriptions",
            {
                "company_id": cid,
                "name": name,
                "amount": amount,
                "interval": interval,
                "start_date": iso(start),
                "included_hours": hours,
                "status": "active",
                "notice_period_days": 30,
            },
            tolerate=(409, 422),
        )
        subs += 1 if out else 0
    log(f"{subs} subscriptions")

    # --- projects --------------------------------------------------------------------
    print("Projects")
    projects: dict[str, str] = {}
    for i, (client, name, hours, amount, status, desc) in enumerate(PROJECTS):
        cid = companies.get(client)
        if not cid:
            continue
        out = api.post(
            "/api/v1/projects",
            {
                "company_id": cid,
                "name": name,
                "description": desc,
                "status": status,
                "budget_hours": hours,
                "budget_amount": amount,
                "budget_period": "total",
                "billable_default": True,
                "currency": "EUR",
                "responsible_user_id": owners[i % len(owners)],
                "start_date": iso(TODAY - timedelta(days=random.randint(40, 200))),
            },
            tolerate=(409, 422),
        )
        if out:
            projects[name] = out["id"]
    log(f"{len(projects)} projects")

    # --- tasks + checklists ----------------------------------------------------------
    print("Tasks")
    tasks: dict[str, str] = {}
    for i, (client, project, title, status, due_in, minutes, desc) in enumerate(TASKS):
        payload: dict[str, Any] = {
            "title": title,
            "status": status,
            "allocated_minutes": minutes,
            "due_date": iso(TODAY + timedelta(days=due_in)),
            "assignee_user_id": owners[i % len(owners)],
        }
        if desc:
            payload["description"] = desc
        if client in companies:
            payload["company_id"] = companies[client]
        if project and project in projects:
            payload["project_id"] = projects[project]
        out = api.post("/api/v1/tasks", payload, tolerate=(409, 422))
        if out:
            tasks[title] = out["id"]
    for title, (heading, items) in CHECKLISTS.items():
        tid = tasks.get(title)
        if not tid:
            continue
        cl = api.post(f"/api/v1/tasks/{tid}/checklists", {"title": heading}, tolerate=(409, 422))
        if not cl:
            continue
        for n, item in enumerate(items):
            made = api.post(
                f"/api/v1/tasks/{tid}/checklists/{cl['id']}/items",
                {"title": item},
                tolerate=(409, 422),
            )
            if made and n < 2:  # tick the first couple, so the bar is visibly part-way
                api.patch(
                    f"/api/v1/tasks/{tid}/checklists/{cl['id']}/items/{made['id']}",
                    {"done": True},
                    tolerate=(404, 422),
                )
    log(f"{len(tasks)} tasks, {len(CHECKLISTS)} checklists")

    # --- time entries ----------------------------------------------------------------
    print("Time entries")
    entry_ids: list[str] = []
    project_names = list(projects)
    client_of = {name: client for client, name, *_ in PROJECTS}
    day = TODAY - timedelta(days=56)
    while day <= TODAY:
        if day.weekday() < 5:
            for m in staff:
                for _ in range(random.randint(1, 3)):
                    pname = random.choice(project_names)
                    out = api.post(
                        "/api/v1/time/entries",
                        {
                            "company_id": companies.get(client_of[pname]),
                            "project_id": projects[pname],
                            "description": random.choice(TIME_DESCRIPTIONS),
                            "billable": random.random() > 0.18,
                            "started_at": at(day, random.choice([9, 10, 11, 13, 14, 15])),
                            "minutes": random.choice([30, 45, 60, 90, 120, 150]),
                            "user_id": m["user_id"],
                        },
                        tolerate=(403, 409, 422),
                    )
                    if out:
                        entry_ids.append(out["id"])
        day += timedelta(days=1)
    log(f"{len(entry_ids)} time entries")
    # Approve the older three quarters, so "goed te keuren" is a small believable number
    # rather than eight weeks of untouched backlog.
    old = entry_ids[: int(len(entry_ids) * 0.75)]
    for i in range(0, len(old), 100):
        api.post("/api/v1/time/entries/approve", {"ids": old[i : i + 100]}, tolerate=(403, 422))
    log(f"{len(old)} approved")

    # --- interactions ----------------------------------------------------------------
    print("Interactions")
    n = 0
    for client, kind, subject, direction, days_ago, body in INTERACTIONS:
        cid = companies.get(client)
        if not cid:
            continue
        people = CONTACTS.get(client) or []
        person = people[0] if people else None
        contact_id = contacts.get(person[2]) if person else None
        when = TODAY - timedelta(days=days_ago)
        moment = at(when, random.randint(9, 16))
        if kind == "email":
            # Protected kind: it has to arrive as a message, not as a form (#262).
            them = f"{person[0]} {person[1]} <{person[2]}>" if person else "info@example.com"
            us = "Sanne de Groot <sanne@example.com>"
            eml = build_eml(
                subject=subject,
                sender=them if direction == "inbound" else us,
                to=us if direction == "inbound" else them,
                when=when,
                body=body,
            )
            if api.upload(
                "/api/v1/interactions/upload-eml",
                f"{subject[:40]}.eml",
                eml,
                {"company_id": cid, "contact_ids": [contact_id] if contact_id else None},
            ):
                n += 1
            continue
        payload: dict[str, Any] = {
            "kind": kind,
            "subject": subject,
            "direction": direction,
            "occurred_at": moment,
            "company_id": cid,
            "body_text": body,
        }
        if contact_id:
            payload["contact_ids"] = [contact_id]
        if api.post("/api/v1/interactions", payload, tolerate=(409, 422)):
            n += 1
    log(f"{n} interactions")

    # --- invoices and quotes ---------------------------------------------------------
    print("Invoices and quotes")
    made = paid = 0
    plan = [
        # (client, days since issue, [(description, qty, unit, price, line kind)], settle?)
        ("Bakkerij Van Loon", 52, [("Ontwikkeluren september", 22, "uur", 95, "hours"),
                                   ("Hosting & onderhoud september", 1, "maand", 89, "subscription")], True),
        ("Nova Fietsen", 44, [("Webshop migratie — eerste termijn", 1, "termijn", 5100, "product"),
                              ("Hosting & onderhoud webshop", 1, "maand", 145, "subscription")], True),
        ("Kliniek Zonneveld", 38, [("Behandelpagina's — tekst en opbouw", 18, "uur", 95, "hours"),
                                   ("Maandelijkse rapportage", 1, "maand", 195, "subscription")], True),
        ("Groenhuis Interieur", 31, [("SEO-onderhoud oktober", 1, "maand", 450, "subscription"),
                                     ("groenhuis-interieur.example 2026–2027", 1, "jaar", 14.50, "domain")], True),
        ("Van Dijk & Partners Advocaten", 24, [("Privacy-audit — analyse", 12, "uur", 95, "hours")], False),
        ("Bouwbedrijf Merelaan", 17, [("Projectenportfolio — ontwerp", 26, "uur", 95, "hours"),
                                      ("Hosting & onderhoud", 1, "maand", 95, "subscription")], False),
        ("Meijer Installatietechniek", 9, [("Conversiemeting inrichten", 8, "uur", 105, "hours")], False),
        ("Camping De Waterlinie", 5, [("Hosting & onderhoud", 1, "maand", 65, "subscription")], False),
    ]
    for client, days_ago, lines, settle in plan:
        cid = companies.get(client)
        if not cid:
            continue
        issue = TODAY - timedelta(days=days_ago)
        out = api.post(
            "/api/v1/invoicing/invoices",
            {
                "company_id": cid,
                "issue_date": iso(issue),
                "due_date": iso(issue + timedelta(days=14)),
                "lines": [
                    {
                        "description": d,
                        "quantity": q,
                        "unit": u,
                        "unit_price": p,
                        "tax_rate_id": low if kind == "domain" else high,
                        "line_kind": kind,
                    }
                    for d, q, u, p, kind in lines
                ],
            },
            tolerate=(409, 422),
        )
        if not out:
            continue
        made += 1
        issued = api.post(f"/api/v1/invoicing/invoices/{out['id']}/issue", {}, tolerate=(409, 422))
        if issued and settle:
            api.post(
                f"/api/v1/invoicing/invoices/{out['id']}/payments",
                {
                    "paid_on": iso(issue + timedelta(days=random.randint(3, 12))),
                    "amount": issued.get("total") or issued.get("total_amount"),
                    "method": "bank_transfer",
                },
                tolerate=(409, 422),
            )
            paid += 1
    # Two drafts, so the Concepten tile is not empty and the editable state is screenshot-able.
    for client, lines in (
        ("Fysiotherapie De Kade", [("Onderhoud CMS-update", 3, "uur", 95, "hours")]),
        ("Nova Fietsen", [("Webshop migratie — tweede termijn", 1, "termijn", 5100, "product")]),
    ):
        cid = companies.get(client)
        if cid:
            api.post(
                "/api/v1/invoicing/invoices",
                {
                    "company_id": cid,
                    "lines": [
                        {"description": d, "quantity": q, "unit": u, "unit_price": p,
                         "tax_rate_id": high, "line_kind": k}
                        for d, q, u, p, k in lines
                    ],
                },
                tolerate=(409, 422),
            )
    log(f"{made} invoices issued, {paid} settled, 2 drafts")

    nq = 0
    for client, reference, lines in (
        ("Atelier Roosmarijn", "Portfoliosite met kleine webshop", [
            ("Ontwerp en vormgeving", 24, "uur", 95),
            ("Bouw portfoliosite", 40, "uur", 95),
            ("Webshopmodule (tot 50 producten)", 1, "stuk", 1450),
            ("Oplevering en instructie", 4, "uur", 95),
        ]),
        ("Restaurant De Vlinder", "Website verhuizen en opfrissen", [
            ("Verhuizing domein, hosting en e-mail", 8, "uur", 95),
            ("Opfrissen vormgeving", 12, "uur", 95),
        ]),
    ):
        cid = companies.get(client)
        if not cid:
            continue
        out = api.post(
            "/api/v1/invoicing/quotes",
            {
                "company_id": cid,
                "reference": reference,
                "issue_date": iso(TODAY - timedelta(days=6)),
                "lines": [
                    {"description": d, "quantity": q, "unit": u, "unit_price": p,
                     "tax_rate_id": high, "line_kind": "product"}
                    for d, q, u, p in lines
                ],
            },
            tolerate=(409, 422),
        )
        if out:
            api.post(f"/api/v1/invoicing/quotes/{out['id']}/issue", {}, tolerate=(409, 422))
            nq += 1
    log(f"{nq} quotes")

    # --- leave -----------------------------------------------------------------------
    print("Leave")
    for year in (TODAY.year, TODAY.year + 1):
        api.post("/api/v1/leave/holidays/import", {"year": year}, tolerate=(409, 422))
    api.post("/api/v1/leave/entitlements/generate", {"year": TODAY.year}, tolerate=(409, 422))
    vac = leave_types.get("vacation_statutory")
    nl_requests = 0
    if vac:
        for email, offset, length, decide in (
            ("thomas@example.com", 12, 4, True),
            ("lisa@example.com", 26, 9, True),
            ("marit@example.com", -18, 4, True),
            # One left pending on purpose, so the approval queue is not an empty state.
            ("youssef@example.com", 40, 11, False),
        ):
            m = by_email.get(email)
            if not m:
                continue
            start = TODAY + timedelta(days=offset)
            out = api.post(
                "/api/v1/leave/requests",
                {
                    "user_id": m["user_id"],
                    "leave_type_id": vac,
                    "start_date": iso(start),
                    "end_date": iso(start + timedelta(days=length)),
                    "note": "Vakantie",
                },
                tolerate=(409, 422),
            )
            if out:
                nl_requests += 1
                if decide:
                    api.post(
                        f"/api/v1/leave/requests/{out['id']}/decide",
                        {"approved": True},
                        tolerate=(409, 422),
                    )
    log(f"{nl_requests} leave requests")

    print("\nWritten:")
    for path, n in sorted(api.created.items()):
        print(f"  {n:>5}  {path}")
    if api.skipped:
        print("\nRefused and carried past (check these — a tolerated failure is still a failure):")
        for key, n in sorted(api.skipped.items()):
            print(f"  {n:>5}  {key}")
            print(f"         {api.skipped_detail[key]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed a throwaway schakl instance with demo data.")
    ap.add_argument("--api", default="http://127.0.0.1:8411")
    ap.add_argument("--host", default="localhost", help="tenant hostname, sent as the Host header")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    api = Api(args.api, args.host)
    api.login(args.email, args.password)
    seed(api)
    return 0


if __name__ == "__main__":
    sys.exit(main())

