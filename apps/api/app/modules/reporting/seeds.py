"""The tone an org starts with (issue #300).

Seeded, not hardcoded. Every sentence below is an *editorial* choice — write warmly, describe
the whole picture rather than the outliers, keep advice out of the client's document — and none
of it is a property of the product. A tenant who reports to industrial buyers rather than local
businesses should be able to read this record, disagree with it, and rewrite it, which is only
possible because it is a row rather than a string in ``prompts.py``.

It is seeded on first use rather than by a migration: a migration must not import the module's
evolving vocabulary (docs/WORKFLOW.md), and an org that never reports should not carry a record
it never asked for.

The text is Dutch because ``nl`` is the default display language (CLAUDE.md §8). It is tenant
data, so it is not translated: an agency writing in English rewrites it in English, which is
the same act as rewriting it at all.
"""

from __future__ import annotations

DEFAULT_TONE_KEY = "standaard"

DEFAULT_TONE_NAME = "Standaard klanttoon"

DEFAULT_TONE_DESCRIPTION = (
    "Warm, betrokken en feitelijk. Beschrijft het totaalbeeld en laat advies en acties "
    "over aan het interne rapport."
)

DEFAULT_TONE_INSTRUCTIONS = """\
Je schrijft de maandrapportage die onze klanten lezen.

De tekst is een toelichting bij de cijfers, geen analyse. Hij moet warm, betrokken,
professioneel en duidelijk zijn, en de klant het gevoel geven dat wij met aandacht naar hun
online prestaties kijken.

Beschrijf het totaalbeeld.
Vat per onderdeel samen wat de cijfers in grote lijnen laten zien. Benoem niet automatisch de
grootste stijgers en dalers. Losse zoekwoorden, bronnen of kanalen noem je alleen als ze
bijdragen aan het bredere beeld of aansluiten bij wat we voor deze klant doen.

Schrijf in de wij-vorm, namens het bureau: "we zien dat", "de cijfers laten zien dat", "het
totaalbeeld geeft".

Blijf feitelijk.
Gebruik alleen de aangeleverde gegevens en het klantprofiel. Doe geen uitspraken over oorzaken
tenzij die uit de gegevens blijken.

Geen advies of acties.
Formuleer geen advies, acties, bespreekpunten, prioriteiten of vervolgstappen. Die horen in het
interne rapport, niet in het document dat de klant leest.

Geen nadruk op dalingen.
Benoem dalingen en schommelingen alleen als ze nodig zijn om het beeld eerlijk te beschrijven.
Doe dat rustig en neutraal, zonder alarmerende woorden.

Gebruik cijfers met mate.
Noem concrete getallen waar ze het beeld verduidelijken. Voorkom dat de tekst een opsomming van
percentages wordt: de cijfers ondersteunen het verhaal, ze zijn niet het verhaal.

Schrijf professioneel, rustig en menselijk. Vermijd superlatieven en grote woorden.

Gebruik geen liggende streepjes in zinnen. Gebruik liever een komma, een dubbele punt of een
punt.

Schrijf de naam van de klant altijd precies zoals die in het klantprofiel staat.

Als de vergelijkingsperiode leeg is, presenteer de cijfers van deze periode dan gewoon als de
prestaties van deze periode. Vergelijk niet met nul.
"""

#: Checked after generation as well as asked for beforehand: a model that ignores an
#: instruction is an ordinary event, and a client document containing the word "advies" is a
#: thing the reviewer should be told about rather than something we hope did not happen.
DEFAULT_BANNED_PHRASES: list[str] = [
    "advies",
    "adviseren",
    "actiepunt",
    "bespreekpunt",
    "kans",
    "kansen",
    "optimaliseren",
    "oppakken",
    "prioriteit",
    "we gaan",
    "we nemen mee",
    "we stellen voor",
    "dit vraagt om",
    "de volgende stap",
    "hier liggen mogelijkheden",
    "het is belangrijk om",
    "dit verdient aandacht",
    "dit vraagt verdere analyse",
    "zorgwekkend",
    "problematisch",
    "dramatisch",
    "terugval",
]

DEFAULT_PREFERRED_PHRASES: list[str] = [
    "valt op",
    "is zichtbaar",
    "laat groei zien",
    "blijft stabiel",
    "beweegt mee binnen het totaalbeeld",
    "zorgt voor verkeer",
    "registreerde conversies",
    "draagt bij aan het totale beeld",
    "laat een rustig beeld zien",
    "laat een wisselend beeld zien",
    "geeft een helder beeld van",
    "sluit aan bij het bredere beeld",
    "komt duidelijk terug in de cijfers",
]

#: The schedule a profile inherits when it says nothing. The fifth of the month, because a
#: report on the first would be produced before the previous month's data has finished
#: settling: GA4 attribution keeps moving for days and Search Console finalises two to three
#: days late (the same lag ``marketing``'s nightly trailing window exists for).
DEFAULT_SCHEDULE: dict = {
    "cadence": "monthly",
    "day_of_month": 5,
    "hour": 8,
    "compare": "year",
    # Review, never auto. The workflow this replaces mailed unreviewed model prose to clients
    # under the agency's brand; making that the default again would be a regression wearing a
    # feature's clothes. An agency that wants it switches it on per client.
    "delivery": "review",
    "publish_to_portal": True,
}
