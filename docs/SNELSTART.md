# SnelStart — the accounting integration

> Epic #377, issue #31. The module lives in `apps/api/app/integrations/snelstart/`;
> the screen in `apps/web/src/routes/(app)/settings/snelstart/`.
>
> **schakl is the CRM and SnelStart is the ledger.** Relations, invoices and articles flow out;
> exactly one thing flows back — whether the money arrived. Nothing here keeps a second copy of
> what has been paid, because that is `invoicing`'s question and two copies of it is how two
> screens start disagreeing about what a client owes.

---

## 1. What was actually verified, and what was not

CLAUDE.md §11 bans writing an integration *from memory*. This one was written against the live
B2B-API v2, with a real koppelsleutel, against a real administration. Every call below was made
and the response read before the code that makes it was written.

| Call | Result |
|---|---|
| `POST auth.snelstart.nl/b2b/token` (`grant_type=clientkey`) | 200, `{access_token, token_type:"bearer", expires_in:3599}`, scopes inside the JWT |
| `GET /companyInfo` | 200 — administration id + name, `huidigBoekjaar`, `artikelcodeSoort`, `artikelcodeMaxLengte`, the seller block |
| `GET /relaties?$filter=Relatiesoort/any(r:r eq 'Eigen')` | 200, exactly the own relation |
| `GET /grootboeken?$top=500` | 200, 233 rows |
| `GET /dagboeken`, `/btwtarieven`, `/kostenplaatsen`, `/landen`, `/artikelomzetgroepen` | 200 |
| `POST /relaties` | 201 — and `REL-0088` for a syntactically-valid-but-fake BTW number |
| `POST /verkoopboekingen` (two rates, two btw rows) | 201 |
| `POST /verkoopboekingen` (same `factuurnummer`) | 400 `BOE-0021` |
| `GET /verkoopfacturen` | 200 — the boeking's `verkoopfactuur`, with `openstaandSaldo` and `vervalDatum` derived from `betalingstermijn` |
| `POST /documenten/Verkoopboekingen` (base64 PDF) | 201 |
| Money as a JSON **string** (`"121.00"`) | 201, read back as the number `121.00` |

Every screen was also driven by hand against that administration — §10 lists exactly what.

**Not exercised against a live credential**: the coupling activation flow (§3) — it needs a
certified `appShortName` and a webhook URL registered with SnelStart, neither of which exists
yet. Everything about it is built from SnelStart's own `/oAuth` documentation page and covered
by `tests/test_snelstart_coupling.py`; the paste path is the one that has actually run.

### 1.1 Two behaviours the documentation does not mention

Both were found by probing, and both are silent.

**`$filter` support is per endpoint.** `/relaties`, `/grootboeken` and `/artikelen` apply it and
*reject an unknown property* (`Could not find a property named 'Nonsense' on type …`).
`/landen` and `/dagboeken` **ignore it completely** and answer `200` with the whole list:

```
GET /landen?$filter=Nonsense eq 'x'     → 200, all 250 countries
GET /dagboeken?$filter=Soort eq 'Verkoop' → 200, all 6 journals
```

A client that trusts the filter and takes `[0]` therefore resolves Nederland for every country
on earth. So `SnelstartClient.fetch` takes a `match` predicate and **re-applies every predicate
locally**; `$filter` is a bandwidth optimisation and never a guarantee. Passing a `filter_`
without a `match` is a deliberate statement that a wrong answer there is merely slower.

**There is no paging metadata.** No `nextLink`, no count; `$top` caps at 500. SnelStart's own
advice is to ask for the next page only while the current one came back full, which is what
`fetch_all` does — and it **refuses to return a prefix**: past `MAX_PAGES` it raises rather than
reporting half a ledger as if it were all of it.

### 1.2 Field limits, measured

`naam` 50 · `factuurnummer` 25 (required) · `omschrijving` 250 · `boekstuk` 25 · `kvkNummer` 12 ·
`iban` 50 · `bic` 15 · `websiteUrl` 100 · attachment 10 MB · `$top` 500.

Enforced in `mapping.py` rather than discovered as `REL-0007` / `BOE-0058` halfway through a
batch. A description one character over is **trimmed**; a *name* is **refused**, because a client
silently renamed to 50 characters is a record its own bookkeeper cannot find.

### 1.3 The day something breaks

Error codes are `{RES}-{NNNN}` and carry the meaning; the message is Dutch and is stored verbatim
on the row, never in an error envelope (§9). The ones worth knowing:

| Code | Means | What we do |
|---|---|---|
| `BOE-0021` | Het factuurnummer bestaat al | **Not a failure.** Go and find it, adopt it |
| `BOE-0058` | Factuurnummer required, max 25 | Refuse before writing |
| `BOE-0060` | The relation is not a customer | The push always keeps `Klant` in `relatiesoort` |
| `BOE-0062` | Verlegd may not be combined with other btw-soorten | A mixed invoice is refused by SnelStart, not by us |
| `BOE-0082` | The btw-soort is missing or disagrees with the ledger's | The two vocabularies were swapped (§4.3) |
| `BOE-0083` | Cannot delete an invoice with payments | We never delete a boeking |
| `REL-0007` | Name required (max 50) | Refused before writing |
| `REL-0008` | Relatiecode already in use | **Not a failure** — see §4.4a |
| `REL-0088` | Invalid BTW number | SnelStart validates it properly; a plausible-looking fake is rejected |
| `ART-0002/3/5` | Article code required / not numeric / already exists | Checked against the administration's own rules first (§4.5) |
| `ALG-0104` | Invalid OData filter | A typo in a filter, on an endpoint that actually applies them |

The documented list on the portal stops at `REL-0039` and is **incomplete** — `REL-0088` is real
and undocumented. Do not treat an unknown code as impossible.

---

## 2. Authentication: two credentials, two owners

```
POST https://auth.snelstart.nl/b2b/token
Content-Type: application/x-www-form-urlencoded

grant_type=clientkey&clientkey={koppelsleutel}
→ {"access_token": "…", "token_type": "bearer", "expires_in": 3599}
```

Then every call to `https://b2bapi.snelstart.nl/v2` carries **both**:

```
Authorization: Bearer {access_token}
Ocp-Apim-Subscription-Key: {subscription key}
```

|  | Whose | Where it lives | Who fixes it |
|---|---|---|---|
| **Subscription key** | the *partner's* — schakl's, or a self-hoster's own | `SCHAKL_SNELSTART_SUBSCRIPTION_KEY`, one per install; overridable per account | the operator |
| **Koppelsleutel** | the *tenant's*, one per administration | `snelstart_accounts.client_key_encrypted`, Fernet | the agency |

**Keeping these apart is not pedantry.** Both are refused with a 401/403 and both read as
"SnelStart said no", but only one of them is something the agency can act on. A free
*Ontwikkeling & Test* subscription key **expires after 90 days**, and when it does, every tenant
on the box starts reporting a credential problem they cannot fix by re-issuing their
koppelsleutel — which is exactly what they will try. So `SnelstartSubscriptionError` is a
separate class with its own i18n key (`errors.snelstart.subscription_rejected`), matched on Azure
API Management's own wording, and the default when uncertain is to blame the koppelsleutel,
because that is the one an agency can re-issue in ten seconds.

The bearer token is **minted, never stored**: cached in memory on the client instance for its
hour, derivable from a credential we already hold, and one fewer secret to rotate. Its `scopes`
claim is read (unverified — we are not authenticating it, SnelStart is, on every call) so the
settings screen can say *"this key cannot write invoices"* before a sync fails halfway.

### Getting a koppelsleutel by hand

SnelStart Web → **Instellingen → Koppelingen** → new key. It names exactly one administration.

---

## 3. Connecting: why cloud needs a broker and self-hosted does not

SnelStart offers a one-click alternative to pasting, and its shape is what forces the design:

1. A **certified** partner registers **one** webhook URL and receives an `appShortName`.
2. The tenant is sent to
   `https://web.snelstart.nl/couplings/activate/{shortname}?referenceKey=…&successUrl=…`.
3. They approve, and SnelStart POSTs to that one URL:
   ```json
   { "KoppelSleutel": "…", "ActionType": "Create|Regenerate|Delete", "ReferenceKey": "…" }
   ```
4. **There is no retry.** Any 2xx is "delivered"; anything else is dropped.

One URL for every tenant is the whole reason there has to be a broker at all.

**Cloud.** The callback lands on the instance apex — a host where *no org resolves* (§5) — so the
request must carry its own tenancy. `referenceKey` is `{org}.{account}.{secret}`, exactly the
shape `app/core/payments/tokens.py` already uses for a payment provider's callback, reused rather
than reinvented because the problem is identical and the cost of getting it wrong is a
cross-tenant write. Five gates, in this order:

1. the reference names the tenant — no hostname, no session, no unscoped lookup;
2. the RLS GUC is bound *before* anything is read, so every read below fails closed;
3. the secret is compared in constant time, and a mismatch is a bare 200 with nothing done —
   never a 401 or 403, which would confirm the account exists;
4. **the body is a hint, never a fact**: the key is believed only after it mints a token and
   names an administration through `/companyInfo`;
5. `Delete` **disconnects, it does not delete the row** — the links, the mappings and the run
   history are the tenant's own audit trail of what was pushed into their books.

**Self-hosted.** Every install has a hostname SnelStart has never heard of and could not post to.
So `SCHAKL_SNELSTART_APP_SHORTNAME` is unset, the activation button does not render *at all*
(#253: a control that always refuses is a broken control), and connecting is a paste. Paste stays
available on cloud too, because a certified shortname is something an agency may not have.

Two details that look like nits and are not:

- **The connect secret is not rotated on receipt.** SnelStart posts again for a `Regenerate`, to
  the same reference. Rotating it would make the *next* regeneration undeliverable — a bug that
  surfaces months later when somebody re-issues a key and nothing happens.
- **Almost everything answers 200.** With no retry behind it, a 4xx buys nothing except a tenant
  watching a connect flow fail silently. The exception is a key that failed its probe: 503, and
  the failure is written to the row so the screen can say why.

The route also carries `license_exempt`: a 402 there would drop a credential the tenant has
already approved, with no mechanism anywhere that would ever deliver it again.

### Configuration

| Setting | Meaning |
|---|---|
| `SCHAKL_SNELSTART_SUBSCRIPTION_KEY` | the partner key. Instance-level, never in an image |
| `SCHAKL_SNELSTART_APP_SHORTNAME` | unset ⇒ no activation flow, paste only |
| `SCHAKL_SNELSTART_WEBHOOK_BASE` | where SnelStart posts. Must be the single URL registered with them; empty ⇒ derived from `base_domain` |

---

## 4. What syncs, and the decisions behind it

| schakl | SnelStart | Direction |
|---|---|---|
| `companies` | `relaties` (`Klant`) | push |
| `companies.invoice_email` | `factuurEmailVersturen.email` | push (the address only — see below) |
| issued `invoices` | `verkoopboekingen` + `btw[]` | push, idempotent |
| the rendered PDF | `documenten` on the boeking | push |
| `invoice_payments` | `verkoopfacturen.openstaandSaldo` | **pull** |
| `invoicing_products.code` | `artikelen.artikelcode` | push |
| `TaxRate.ledger_code` | `grootboeken` | pull → picker |
| seller identity | `companyInfo` + the `Eigen` relation | pull → compare |
| `landen`, `dagboeken`, `kostenplaatsen`, `btwtarieven`, `artikelomzetgroepen` | reference cache | pull |

### 4.1 A `verkoopboeking`, not a `verkooporder`

An order is a document SnelStart lays out and **prints**; a boeking is the ledger entry. schakl
already rendered this invoice, already owns its number and has usually already mailed the PDF —
pushing an order would make SnelStart print a second, differently-designed copy of a document the
client is holding. The boeking books the money and takes our PDF as its attachment, which is what
an accountant asks for at year end.

### 4.2 Never twice

#31 calls a duplicate invoice a real-world incident, so the guard is structural rather than
careful. Four layers, each covering a failure the previous cannot:

1. **The stored link** — we already pushed it.
2. **A lookup by number** — no link, but the number may still be there: from a previous install,
   from a bookkeeper typing it in, or from a push whose answer we never saw. Asked through
   `/verkoopfacturen`, because `verkoopboekingen` has no list operation at all.
3. **`BOE-0021`** — SnelStart refusing a duplicate number is **not a failure**; it is the answer
   to *"is it already there?"*, and we go and adopt it.
4. **An unknown write** — a timeout or a 502 means the boeking may exist. We look, and only then
   decide. This is the one #31 singles out, because a blind retry here is how the incident
   actually happens.

A boeking under our number that we did **not** write is adopted and left alone, never
overwritten. Somebody wrote it, and silently replacing a bookkeeper's entry is the failure every
mirroring integration in this codebase is warned about. `push_hash` stays `NULL` on an adopted
row precisely because we cannot claim the payload matches.

**And an adopted boeking whose amount is not the invoice's is `drift`.** Adopting is right;
adopting *silently* would leave schakl showing €635,25 and the ledger €1.428,00 under one number
with nothing anywhere saying so — the silent half of a silent overwrite. So it is its own run
outcome (not a failure: nothing went wrong and nothing needs retrying), it rides the run's own
attention list, and it has a number on the client's panel. A push that meets its *own* previous
work is `adopted` and stays quiet, because a signal that fires on every match is worthless
within a week.

### 4.3 The btw-soort is derived, not typed

`GET /btwtarieven` returns date-ranged percentages per `btwSoort`, so a schakl rate of 21,00 on an
invoice dated today *is* `Hoog` by lookup. Asking an admin to map rates by hand would be offering
them a way to get it wrong about tax. It also has to be a lookup rather than a constant: the Dutch
low rate was **6% until 2019 and 9% after**, and an invoice dated 2018 must still book as `Laag`.

A rate the administration's table cannot confirm falls back on the schakl category — and **says
so**. `guessed_rates` rides on the push result and into the sync run's counts, because "we
guessed how to tax this" is a sentence a finance integration has to say out loud.

Two vocabularies, and they are different:

| Where | Values |
|---|---|
| `boekingsregels[].btwSoort` | `Geen` · `Laag` · `Hoog` · `Overig` |
| `btw[].btwSoort` | `Geen` · `VerkopenLaag` · `VerkopenHoog` · `VerkopenOverig` · `VerkopenVerlegd` |

Swapping them answers `BOE-0082`. Reverse charge carries `Geen` on the line and `VerkopenVerlegd`
on the document — the line has no tax and the document declares that the tax was shifted.

What genuinely **cannot** be derived is which revenue account each rate books to, and that is the
only thing the settings screen asks for. It rides on `TaxRate.ledger_code`, which already existed
for exactly this, stored as the **grootboeknummer** (`8200`) rather than the uuid: that is what a
bookkeeper says out loud and what survives a restore into a fresh administration. The uuid is
resolved from the reference cache at push time, which is why a reference sync is a prerequisite
for a push rather than a nicety.

The ledger picker offers `Verkopen*` / `Dienstverlening*` accounts plus the 8000–8999 revenue
band, and nothing else. Offering all 233 accounts would be offering a way to book a sales line to
*Btw af te dragen hoog* — which balances, reconciles, and is wrong.

### 4.4 Matching proposes; a human disposes

The first connect is the dangerous moment: 200 relations against 180 companies is an overlap
nobody can eyeball. So matching is ordered by what actually identifies:

| Match on | Applied automatically? |
|---|---|
| `kvkNummer` → `coc_number` | yes |
| `btwNummer` → `vat_number` | yes |
| `relatiecode` → `client_number` | yes |
| `email` → `invoice_email` | proposed |
| `naam` → `name` | proposed |

*Jansen bv* and *Jansen Transport bv* are two companies and one substring. And an identifier that
matches **two** schakl companies stops being an identifier: it matches nothing rather than
whichever row was loaded first, because picking one is how an invoice goes to the wrong company
with nothing on any screen to suggest it.

A relation nothing matches is stored as `unlinked` — a real state, and how an agency finds out
their bookkeeper has forty clients the CRM has never heard of.

### 4.4a A relatiecode collision costs a client record, so it does not

schakl's `client_number` and SnelStart's `relatiecode` are two numbering systems that were never
coordinated, so a collision is ordinary — and it used to fail the whole create, reported as
*"SnelStart weigert dit verzoek. Relatiecode reeds in gebruik"* with nothing an admin could do
but renumber their CRM. **The relation is the requirement; the shared number is a convenience.**

So `REL-0008` sends us to look at who holds it, compared on the identifiers that identify (KvK,
then VAT, then an exact name):

- **the same client** → adopt them. This is the usual case: the bookkeeper entered them first.
- **somebody else** → create without a code and let SnelStart allocate its own. The link then
  records the code it really got, so the screen tells the truth rather than what we asked for.

The comparison here is deliberately stricter than §4.4's: nothing about it reaches a human for
review, so a *guess* would be applied silently.

### 4.4b What is not a client

Every administration ships three rows that are not clients: the agency's own relation
(`Relatiesoort` contains `Eigen` — in a fresh administration still called
*"<Vul hier uw bedrijfsnaam in>"*) and the reserved placeholders `-1 Leverancier onbekend` and
`-2 Klant onbekend`. SnelStart reserves negative relatiecodes for exactly this. All three are
filtered out of the review, because a list an admin has to read row by row is only worth reading
if every row on it is a real question.

One schakl record pairs with one SnelStart record per administration, and the partial unique
index says so. Pairing by hand therefore refuses (409 `errors.snelstart.already_linked`) rather
than letting the index enforce it as a 500 — and the picker does not offer a client who is
already taken, since a control that can only refuse should not be drawn (#253).

### 4.5 Pushing a relation merges; it does not replace

`PUT /relaties/{id}` replaces the whole record. A payload built only from schakl's fields would
blank the memo, the credit limit and the direct-debit mandate every time somebody edited a phone
number in the CRM. So the relation is read back first and schakl's own fields are laid on top —
and an **empty** schakl field does not blank SnelStart's, which is §18's "absent means leave
alone" applied across a system boundary.

`relatiesoort` always keeps `Klant` (`BOE-0060`) and never loses `Leverancier`. `relatiecode` is
set only on a *create*: renumbering an existing relation would rewrite what appears on every
document that mentions it.

`factuurEmailVersturen.email` is supplied; `shouldSend` is left alone. Whether *SnelStart* mails
the invoice is the bookkeeper's decision, and turning it on here would double-send a document
schakl already sent.

The digest that decides whether to write at all is taken over **schakl's own contribution**, not
over the merged payload — a merged payload legitimately differs between a create and an update,
and hashing it made every nightly sync rewrite five hundred unchanged relations.

### 4.6 Articles, and `invoicing_products.code`

`invoicing_products` gains `code` — schakl's own article code, unique per org where set, nullable
because every product predating this has none and inventing one would put a number in somebody's
article file that schakl would have to keep guessing identically for ever.

Its rules are **per administration**, not properties of the API: `companyInfo.artikelcodeSoort`
is `Numeriek` or `Alfanumeriek` and `artikelcodeMaxLengte` is a number, both read at verify time
and stored on the account. So a product called `WEB-01` is perfectly valid in one administration
and refused in another, and it is refused **before** the write, by name, rather than discovered as
`ART-0003` halfway through a batch.

A product with no code is skipped and counted, which is what tells an agency to go and fill them
in.

### 4.7 Payments: the one thing that flows back

The bank statement is matched in SnelStart, so SnelStart is the only place that knows an invoice
was paid — and *"who hasn't paid"* is a CRM question. `verkoopfacturen.openstaandSaldo` is the
answer, read wholesale (an invoice becoming paid changes `modifiedOn` unreliably, and a payment
we failed to notice is worse than a read we did not need).

What lands in schakl is an ordinary `InvoicePayment` (method `bank`, dated today in the org's
zone) — **not** a status flipped directly. Everything downstream — `_settle`, `invoice.paid`, the
dunning cron, the client portal — then behaves exactly as it does for a payment typed in by hand,
because as far as it can tell that is what it is. A second way of marking an invoice paid is how
two screens start disagreeing.

Two rules:

- **A partial payment is booked too.** A client who paid half is not a client who paid, and an
  integration that only recognised "fully settled" would leave the invoice looking untouched.
- **It only ever books money *in*.** If SnelStart says *more* is owed than schakl thinks, that is
  a human decision about somebody's books; an automatic reversal of a recorded payment is not a
  thing an unattended cron should be able to do.

---

## 5. Permissions

Three keys, because holding the credential, acting through it and writing with it are three
grants an agency hands to different people:

| Key | What it opens |
|---|---|
| `snelstart.settings.manage` | connect, rotate, verify, remove, configure |
| `snelstart.sync.run` | read the administration: reference data, relation review, payment reconcile |
| `snelstart.ledger.write` | push. The one that changes somebody else's books |

All admin-only by default, none ever granted to `client`.

There is deliberately **no** permission for "push an invoice". That act is invoicing's and is
already gated, so the push routes declare **both** `snelstart.ledger.write` and
`invoicing.invoice.write` — either alone would let somebody do half of something they should not
be able to do at all.

The company panel is gated on `snelstart.sync.run` and never on `invoicing.invoice.read`, which
#266 put a client-portal login behind at `:own`: a client has no business knowing which accounting
package their agency uses.

---

## 6. Failures are visible

#31's hardest requirement, and the one most integrations skip. `snelstart_sync_runs` records what
each run did (`counts`) and what it could not do (`errors`, capped at 50 with the count staying
exact), and the settings screen renders it. `ok` is `True` only when everything the run set out
to do happened — a run that pushed 37 of 40 is **not** ok, it is a run with three things still to
do, and rounding that up to success is how a client goes uninvoiced for a month.

A per-row failure is that row's: the batch records it and carries on. A whole-run failure lands on
the account's `last_error` — but the red `status` is earned only by a **rejected credential**.
SnelStart being unreachable for ninety seconds is not a reason to tell an agency their connection
is broken; the text is recorded either way, and the status is what a screen shouts about.

The nightly cron notifies once per account per night (`snelstart.sync.failed`) rather than once
per failed row.

### The savepoint that could not be

§18 says each row of a bulk write runs in its own `SAVEPOINT`; §11 says every in-request outbound
call is wrapped in `ctx.release_db()`. **Both cannot hold here**: `release_db` *commits* on entry,
and a commit ends the enclosing savepoint, so a `begin_nested()` around a push is closed by the
first HTTP call inside it and its eventual `.commit()` raises `ResourceClosedError`. A test caught
exactly that.

`release_db` wins. Holding a pooled connection across forty outbound calls is the pool-drain §11
exists to prevent and it is worst in a long batch — and the commit it performs gives what the
savepoint was for anyway: each row's work is durable before the next row starts. What the savepoint
did provide and had to be replaced is recovery from a *database* error, so a `SQLAlchemyError`
rolls the session back and the loop carries on (`SnelstartSyncService._row`). The payments loop
still uses a real savepoint, because `add_payment` is pure database work and nothing commits
underneath it.

---

## 7. Money never becomes a float

#31 says money is `Decimal`. Both obvious encodings break that: `float(amount)` openly, and
`json.loads(str(amount))` **silently** — it parses the text straight back into a Python float, so
`Decimal("1428.00")` leaves as `1428.0` and awkward cents leave as whatever binary floating point
landed on. A test caught it.

So an amount travels as its own decimal text, and the live API accepts it:

```
POST /verkoopboekingen  {"factuurbedrag": "121.00", …}   → 201
GET  /verkoopboekingen/{id}                              → {"factuurbedrag": 121.00, …}
```

.NET parses a JSON string into a `decimal` exactly. That is the one encoding in which no float
exists at any point on the wire, and `tests/snelstart_fake.py` reproduces the normalisation so a
test asserts against the shape the real API returns.

Per-line nets come from `invoicing.calc.line_nets`, shared with the UBL export rather than
re-derived: on a tax-inclusive document the sum of independently rounded line nets is not the
group base, and a second implementation of that reconciliation is how two exports of one invoice
disagree by a cent with an accountant reading both.

---

## 8. Tables

| Table | Holds |
|---|---|
| `snelstart_accounts` | one administration: the credential, what it opens, and what this connection does |
| `snelstart_links` | one pairing per record — **what we decided** (`push_hash`, `pushed_at`) and **what we observed** (`observed`, `observed_at`) in separate columns |
| `snelstart_refs` | the administration's own vocabulary, cached. Never authoritative |
| `snelstart_sync_runs` | what each run did and could not do |

All org-scoped and RLS-forced. `snelstart_links.company_id` is a real FK because it is the company
horizon's anchor (#285); `local_id` carries none, because it points across a module boundary and
because an `unlinked` relation has no local row at all.

`SnelstartLinkStatus` is six values rather than a boolean — `pending`, `active`, `drift`,
`missing`, `error`, `unlinked` — because each needs a different button, which is the test for
whether a status column has earned its values.

---

## 9. The generic seam

`invoicing` shipped `AccountingProvider` for #31 before any provider existed, along with
`GET /invoicing/providers`, `POST /invoicing/invoices/{id}/export?provider=…` and
`GET /invoicing/invoices/{id}/refs`. `provider.py` fills it in, so all three light up with **no
edit to invoicing**.

There are therefore two ways to push one invoice, and they are not a duplication: this module's
own route takes a selection, runs each row separately, attaches PDFs and writes a readable run
log; the generic one is reached from the invoice's own screen by somebody who does not know or
care which package is connected. Both end in `SnelstartSyncService.push_invoice`, so they cannot
disagree about idempotency.

The generic export **refuses to guess** which administration when a tenant holds two, rather than
booking into whichever was created first.

---

## 10. What was driven in a browser

Not a smoke test: the whole flow, against the live *Marcusse Online Marketing* administration,
through the real screens.

Connect and verify (`companyInfo` named the administration and its financial year) · reference
sync (233 ledgers, 6 journals, 252 countries, 8 revenue groups, 19 VAT rates) · relations pushed,
one of them adopted over a `REL-0008` collision · invoices booked, one of them adopted over a
number already in the books, with per-rate ledger accounts, derived btw-soorten and the rendered
PDF attached · a real `bankboeking` created in SnelStart settling one invoice and part-paying
another, pulled back as ordinary `InvoicePayment`s · articles pushed, the codeless one skipped ·
the relation review worked by hand, from three noise rows down to one real decision and then
paired in a click · and every sync re-run until it reported all-unchanged.

The screen also renders from the **built image**, not just from `vite dev` — which is what
proves the SSR bundle is self-contained (`docs/WEB.md`).

## 11. What to check the day a certified key arrives

The paste path has run against a live administration; the coupling flow has not. When a
production `appShortName` and webhook URL exist:

1. Register `https://{apex}/api/v1/snelstart/coupling/callback` with SnelStart and set
   `SCHAKL_SNELSTART_APP_SHORTNAME`.
2. Confirm the edge actually forwards it — behind Cloudflare Zero Trust that is a rule somebody
   has to add, and the URL is shown on the settings screen precisely so they can.
3. Run the activation link and check the field casing on the real POST body (`KoppelSleutel`
   vs `koppelSleutel`); both are accepted, but confirm which SnelStart sends.
4. Check `ActionType: "Regenerate"` really arrives at the same reference.
5. Confirm a production subscription key does not expire the way the developer one does, and
   write the renewal into whatever calendar the operator keeps.
