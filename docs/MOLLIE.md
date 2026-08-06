# Mollie integration

> The `mollie` module (epic #269, issue #267): the tenant's own Mollie account, and the hosted
> checkout a client pays an invoice on. Business-licensed (`sku="mollie"`). Read this before
> changing anything under `apps/api/app/modules/mollie/`,
> `apps/web/src/lib/modules/mollie/` or `apps/web/src/routes/(app)/settings/mollie/`.

Sibling to `docs/PAYMENTS.md`, and the two halves meet at exactly one place: the
`PaymentProvider` protocol in `app/core/payments/backend.py`. **This document is Mollie's half.**
What a payment *means* — which invoice it settles, on what day, in what ledger row — is
`invoicing`'s, and this module cannot see it (CLAUDE.md §6). Read `docs/PAYMENTS.md` first if
you are here to change the settle path; nothing in it is Mollie-specific.

Everything below is sourced from Mollie's official reference at `docs.mollie.com` (fetched
2026-08-05). Every page there has a `.md` twin (append `.md` to any reference URL), the index is
`https://docs.mollie.com/llms.txt`, and each endpoint page embeds the **OpenAPI 3.1 definition**
for that endpoint in a fenced block. That embedded spec is the authority used here, not prose and
not memory.

## 1. What it is for, and what has never been run

An agency e-mails a PDF and waits for a bank transfer. This module lets the client pay the
invoice on Mollie's own hosted checkout — iDEAL, Bancontact, cards, SEPA direct debit, PayPal,
whatever their Mollie profile has enabled — and lets the payment land back on the invoice as an
ordinary `InvoicePayment` row without a human retyping it. schakl never touches card data and
holds **no PCI scope at all**: the payer is redirected to Mollie, and what comes back here is an
id.

**Nothing in this module has ever been exercised against a live Mollie account.** It is written
from Mollie's official API documentation — a real document, and an embedded machine-readable
spec, which is the distinction CLAUDE.md §11's ban actually draws — but the issue's own blockers
asked for a credential as well, and only the documentation exists. So every response parse is
defensive: a missing field is `None`, `None` means *not reported* and never `False` or zero, an
unknown status is treated as still-in-flight rather than guessed into a final state, and nothing
assumes a shape it has not been shown.

### The first-credential checklist

Run these in order the day a credential arrives — a `test_…` key is enough for all but two of
them. Each is a place where the code committed to a reading the document does not fully
guarantee, and where being wrong is silent rather than loud.

1. **A real webhook body.** Capture one. The documented contract is
   `application/x-www-form-urlencoded` with a single field `id`, and `references_in_webhook`
   branches on the content type. Confirm the header Mollie actually sends and that the body
   really is `id=tr_…` and nothing else. If it ever arrives as JSON, the JSON branch reads
   `id` and `data.id` — and **only the id**, never the status.
2. **The payment-id character set.** `_PAYMENT_ID_RE` is `^tr_[A-Za-z0-9]+$`; Mollie's own spec
   says only `^tr_.+$`. Ours is stricter, and if a real id ever carries a hyphen or an
   underscore, `fetch_payment` returns `None` and the intent reads *"provider does not know
   this payment"* forever — a wrong answer wearing a plausible one's clothes. Collect a dozen
   real ids before trusting the narrower pattern; widening it is a one-line change.
3. **`GET /methods`'s envelope.** `verify()` reads `_embedded.methods[].id`. Confirm that
   nesting, and confirm the endpoint answers **with a plain API key** (the spec lists API key,
   advanced access token and OAuth for it — this is the whole reason it is the probe, §3).
   Note the two documented mode differences while you are there: in test mode Mollie returns
   *pending as well as enabled* methods (and, if none has ever been requested, the most popular
   ones); in live mode only fully enabled ones. So a test key legitimately reports methods the
   live key will not.
4. **`_links.checkout.href` on a fresh payment, and its absence on a final one.** The whole
   "clear the link once it is final" rule in `payments.py` rests on it being present while a
   payment is payable and gone afterwards. Create one, read it, force it to `expired` on the
   test checkout screen, read it again.
5. **`paidAt` — the exact string, and the offset.** `_parse_instant` takes ISO-8601 and swaps a
   trailing `Z`. The value is converted to the org's zone to pick a `paid_on` calendar day, so a
   naive-parsed instant misfiles a late-evening payment by one day, half the year. Verify a
   payment made after 22:00 Amsterdam time books on the right date.
6. **Which failure field is actually populated.** `_failure_detail` tries
   `details.failureMessage`, `details.failureReason`, `details.bankReason`, then
   `statusReason.message`. Refuse a test card (§8), read the real payload, and keep the field
   that appears — it is what the agency reads on the intent row when a client says the payment
   did not work.
7. **The `Idempotency-Key` round trip.** We send our own intent UUID as the key on
   `POST /payments`. Confirm Mollie accepts it, and capture a deliberate replay: the documented
   behaviour is the cached response plus an `Idempotent-Replayed: true` header (cache lifetime
   one hour, keyed to the credential). We do not read that header today; if it turns out to be
   the only way to tell a replay from a fresh create, that is a line worth adding.
8. **The unknown-id answer.** `fetch_payment` passes `allow_404=True` and turns a 404 into
   `None`, which the callback path treats as *"somebody else's payment, or a forgery"* and
   answers 200 to. Confirm Mollie really answers 404 — and not 403 or 422 — for a well-formed
   `tr_…` this credential does not own. A 403 would be read as `MollieAuthError` and would
   report the tenant's working key as rejected.
9. **Whether an auth failure is 401 or 403.** Both are mapped to `MollieAuthError` (which does
   not retry, correctly), but only one of them should be reachable with a revoked key. Send a
   deliberately wrong key and record the status *and* the body's `title`/`detail` — that text
   is what lands on `last_error` and is the whole content of the settings screen's failure
   state.
10. **The locale values.** `_LOCALES` maps our two-letter locales onto Mollie's `xx_XX` list
    from the Create-payment reference. An unsupported value would 422 the **whole create**, not
    degrade — so confirm `nl_NL` and `en_GB` are accepted, and that omitting `locale` entirely
    falls back to the browser language as documented.
11. **`method` omitted really shows the picker.** §4 is the design decision this rests on: we
    send no `method`, so Mollie's checkout shows its own selector and its own retry-with-another-
    method fallback. Confirm the checkout page for a payment created with no `method`.
12. **Live mode, once, with a real euro.** The two things a test key cannot prove are that a
    `live_…` key verifies at all and that `mode` comes back `live` on the payment object — which
    is the flag `payments.py` reads to decide whether to write a ledger row. Pay a small invoice
    for real, watch the callback arrive, and check both the `InvoicePayment` row and the
    activity trail entry.

Until at least 1, 2, 4 and 5 are confirmed, treat a green settings screen as evidence that the
credential is valid and nothing more.

## 2. The credential

`mollie_accounts` holds one row per Mollie API key the tenant has handed us — org-scoped,
RLS-forced, `AuditableMixin` (rotating the credential that collects an agency's money is exactly
the change somebody needs attributed later; the key itself is never part of the trail, only that
it changed and by whom).

**A row, not a settings singleton**, for the reason `cloudflare_accounts` and `oxxa_accounts`
are rows — and more sharply here: an agency integrating holds a **live key and a test key at the
same time**. A singleton would have made the second one an overwrite, which for a payment
credential means either taking real money in a test or failing to take any in production.

- **Mollie keys are self-typed.** `test_…` and `live_…` are separate, fully isolated datasets,
  and one key belongs to exactly one of them (and to exactly one website profile). So
  `MollieAccount.mode` is **derived** from the key's own prefix on every save (`client.mode_of`)
  and never entered: a field an admin can get wrong about money is a field that should not
  exist. A rotation from live to test moves the mode with it, or an agency believes it is taking
  money it is not.
- Anything unrecognised reads as **`live`**. Erring towards "this is real money" makes a misread
  key settle nothing; erring the other way would book a real payment as a test and silently lose
  it.
- The `testmode` request parameter exists only for organization-level and OAuth credentials and
  **must not be sent with an API key**. It never is.
- The key is Fernet at rest (`app.core.crypto`, the `*_encrypted` convention from
  `docs/GOOGLE.md`), write-only through the API: `MollieAccountRead` carries
  `api_key_configured` and never the value. It is decrypted in exactly one function,
  `service.client_for`, which is what makes *"the key is read once"* checkable.
- `MollieAccountCreate` **refuses anything not shaped like a Mollie key**
  (`errors.mollie.not_an_api_key`). Not security — the credential proves itself by working — but
  a paste of the wrong secret is a mistake worth catching before it is encrypted, stored, and
  then reported as "Mollie rejected your credential" with no hint that it never was one.
- A `SCHAKL_ENCRYPTION_KEY` rotation leaves an unreadable secret. That is
  `errors.mollie_credential_unreadable` (409), not a 500: the fix is re-entering the key, not
  retrying.

### Rotation clears what the old key vouched for

Rotating the API key resets `methods`, `last_verified_at`, `status` and `last_error` — a stale
"verified" badge must not speak for a key nobody has tested — **and regenerates
`webhook_secret`**, so the callback URL moves too. A key is usually rotated *because* it leaked;
leaving the old URL answering would keep one half of a compromised pair alive.

### There is no `profile_id` column

Issue #267 asked for one, and it is not there. Mollie's Profiles API (`GET /profiles/{id}`,
`GET /profiles/me`) is documented as reachable with an **advanced access token or OAuth** —
a plain API key is not listed. A key already *belongs* to one profile, so the question is
answerable in principle and not by us. Storing a column we could only sometimes fill would have
been worse than not having it; what actually tells two keys apart on the settings screen is the
tenant's own `name` plus the observed `methods` list.

`methods` is an **observation, never a setting**: enabling a payment method happens in Mollie's
own dashboard, and a list stored here that pretended otherwise would be a second source of truth
(CLAUDE.md §10). Note that `GET /methods` returns only **online** methods, and by default only
those supporting EUR.

### Deleting an account

Removes the credential and touches nothing at Mollie. The payment intents it opened **stay**:
they are invoicing's rows, they carry the ledger link that already settled, and deleting the
history of how an invoice was paid because somebody rotated a key would be the wrong kind of
tidy. Their `account_id` simply points at a row that is gone, which is exactly what a bare UUID
(§6) is for.

## 3. Transport, and `verify`

| | |
|---|---|
| Base URL | `https://api.mollie.com/v2`, HTTPS only, TLS 1.2 minimum |
| Auth | `Authorization: Bearer <api key>` |
| Request body | `application/json` |
| Response | `application/json` or `application/hal+json` |

`_TIMEOUT` is 5 s connect / 20 s read / 20 s write. Mollie is a dependency of a **button**, not
of a page load, and its own webhook budget is 15 seconds — a read that has not answered in 20 is
not going to save the request it is holding. Every outbound call — `verify`, `create_payment`,
`fetch_payment` — is made inside `ctx.release_db()`: a connection held across a twenty-second
external call is exactly the pool-drain CLAUDE.md §11 exists to prevent.

**Reads retry once; writes never.** `_RETRY_STATUSES` is `{429, 500, 502, 503, 504}` and
`attempts` is `2 if method == "GET" else 1`. A blind retry of `POST /payments` is a second
checkout link for one invoice, which is a refund conversation (§5).

A response over `MAX_RESPONSE_BYTES` (4 MB) is refused **before** it is decoded — CLAUDE.md
§17's rule that every cap is checked before the work it bounds. A payment object is a few
kilobytes; a megabyte is a provider fault or an edge error page.

### `verify` calls `GET /methods`, and why not something else

`POST /mollie/accounts/{id}/verify` sends one request: `GET /v2/methods`. Three reasons, and
the third is the decisive one:

- it is the cheapest authenticated read Mollie offers, and it is **not paginated**;
- its answer is the one an admin actually wants on the settings screen — *which methods can
  this key take?* — rather than a bare "ok";
- it is reachable **with an API key**. The obvious alternative, "which profile is this?", is
  not: the Profiles API needs an advanced access token or OAuth (§2).

**Verify never raises.** `require_context` rolls the session back on any exception, so raising
would discard the very row that records what Mollie said. The service returns
`MollieAccountVerifyResult(ok=False, error=…)`, the row keeps Mollie's own untranslatable words
on `last_error`, and the screen renders them. `ok=False` with the row still saved is a real and
common state — telling somebody their key is wrong is more useful than refusing to remember what
they typed. The same shape `oxxa`'s and `cloudflare`'s verify use, for the same reason.

Mollie's own answer for `mode` wins over our prefix reading of the key. They agree in every
documented case; if they ever did not, Mollie is right.

## 4. Creating a payment: what we send, and what we leave out

`POST /v2/payments`. The required fields are `amount` `{currency, value}`, `description` and
`redirectUrl`.

```json
{
  "amount":      {"currency": "EUR", "value": "1250.00"},
  "description": "2026-0041 — Breik",
  "redirectUrl": "https://klant.bureau.nl/invoices/<uuid>",
  "webhookUrl":  "https://klant.bureau.nl/api/v1/invoicing/payments/webhook/mollie/<token>",
  "locale":      "nl_NL",
  "metadata":    {"invoice_id": "…", "invoice_number": "2026-0041", "intent_id": "…"}
}
```

- **`amount.value` is a decimal *string* with the exact decimals**, and the currency is an
  ISO-4217 code. `_amount` formats a `Decimal` at two places; a float never touches this
  boundary in either direction. The amount is always the invoice's **outstanding**, computed
  server-side — `InvoicePaymentIntentCreate` carries no amount at all.
- **`description` is what the payer sees on their bank or card statement.** Mollie's cap is 255
  and card networks truncate far harder, so it leads with the number a client can match:
  `"<invoice number> — <org name>"`.
- **`redirectUrl`** is where the payer lands **whatever the outcome** — so it is the invoice's
  own page in the client portal, not a thank-you page. `cancelUrl` (the explicit-cancel
  variant) is supported by the seam and not sent today; the portal page is the honest landing
  for a cancel too.
- **`webhookUrl` must be internet-reachable** (Mollie rejects `localhost`), which is §7's
  deployment note and the single most likely thing to be wrong on a real install.
- **`metadata`** (~1 kB, echoed back on every read) carries the invoice id, its number and the
  intent id, so a human staring at Mollie's dashboard can find the invoice. It is **never how a
  callback is resolved**: an id we chose is not evidence, and the mapping lives in our own table
  keyed on Mollie's `id`.
- **`locale`** is mapped from the invoice's own locale onto Mollie's `xx_XX` list. A locale we
  cannot map is simply **omitted**, and Mollie falls back to the payer's browser language —
  which beats guessing `en_US` at a Dutch payer.

### `method` is deliberately not sent

This was #267's one open question, and it is answered the way the issue leaned. Omitting
`method` makes Mollie's hosted checkout show **its own picker**, which means:

- the shopper chooses, and the agency configures which methods exist in **Mollie's** dashboard —
  schakl grows no second place to get that wrong, and no settings screen that can drift out of
  sync with the account it describes;
- Mollie's own **retry fallback** stays available. With no method pinned, a failed or cancelled
  attempt sends the payer back to the checkout to pick another; the payment only becomes
  `canceled` when they cancel *there*. Pinning a method removes that entirely — a failed pinned
  payment cannot be retried with anything else.

The seam is capable of pinning (it would be one key in the body); nothing calls for it, and
adding a per-invoice method selector would be a UI that makes payments fail more often.

### `profileId` is not sent either

The spec is explicit: `profileId` **must not be sent with an API key** — the key already names
the profile. Sending it is an error, not a redundancy.

### What we do not use

- **`?include=details.qrCode`.** Mollie can return a QR for iDEAL, Bancontact and bank transfer.
  It is not what #268 wants: the QR an invoice prints encodes the **portal URL**, not a
  checkout, because a checkout URL on paper is a bearer credential anyone who picks up the paper
  can spend. `docs/PAYMENTS.md` §9 has the full reasoning.
- **`dueDate`** (write-only, bank transfer). Invoicing already has its own due date and its own
  reminder cron; a second one at the provider would be a second thing to keep in step.
- **The Payment Links API.** A payment link is a second object with its own lifecycle; the
  intent row plus a fresh checkout covers the same ground with nothing extra to reconcile.

### Redirecting the payer

`_links.checkout.href` is the URL to send the payer to, and it is present only while the payment
is payable. Send them there with an HTTP **GET** — a `303 See Other` if you are redirecting
server-side. A `POST` redirect breaks some methods and issuers. Today the web layer opens the
stored `checkout_url` directly, which is a GET by construction; anything that later wraps it must
keep that property.

## 5. Idempotency

Mollie accepts an **`Idempotency-Key`** header on any POST: a UUID4, cached for **one hour**, and
keyed to the credential used. A replay returns the original response with
`Idempotent-Replayed: true`. The same key against a different endpoint or different parameters is
a **400**; the same key while the first request is still in flight is a **409**.

We send one on `POST /payments`, and it is the **intent's own UUID** — generated before the call,
used as `PaymentRequest.reference`, and then written as the intent's primary key. So a transport
retry inside the hour cannot open a second checkout, and the key is derivable rather than
remembered.

Mollie's own guidance is that a duplicated *regular* payment is fairly harmless (one simply
expires) and that the dangerous cases are recurring payments, subscriptions and partial refunds.
We send a key anyway, and keep the write out of the retry allowlist regardless, because a
duplicate here is not harmless *to us*: it litters a client's invoice with two live payment links
for one debt, and a client who pays both is owed a refund.

Note what the header does **not** cover: Mollie's cache is an hour long, and the reconcile cron
runs for days. Nothing about idempotency at Mollie protects the *ledger* — that is the row lock
plus the partial unique index in `docs/PAYMENTS.md` §5, which is a different mechanism solving a
different race.

## 6. The webhook contract

**The classic, per-payment `webhookUrl` contract**, which is the one we use:

- Mollie POSTs `application/x-www-form-urlencoded` with **one field, `id`** —
  `id=tr_5B8cwPMGnU6qLbRvo7qEZo`. **No status, no amount, no signature.**
- **The re-fetch is the authentication.** Mollie's own words: *"fake calls to your webhook will
  never result in orders being processed without being actually paid."* There is nothing in the
  body to forge, because the body asserts nothing.
- **Return 200 — including for an id you do not recognise**, so nothing is leaked about what
  exists here. `handle_webhook` does exactly that.
- **Timeout 15 seconds. 10 attempts over 26 hours**, at roughly 0, 1 m, 3 m, 7 m, 15 m, 31 m,
  1 h, 2 h, 4 h and 26 h. That schedule is why a 503 from us is a *recovery mechanism* rather
  than a failure: it puts the callback back in Mollie's queue.
- **Do not IP-allowlist.** Mollie says so explicitly; the published range list moves.
- **A 301/302 redirect drops the POST body** — only 307/308 preserve it. Anything in front of the
  API that rewrites the callback URL (a trailing slash, an http→https bump) must use 307/308 or
  the body arrives empty and parses to nothing.
- Fired for `paid`, `authorized`, `expired`, `failed` and `canceled`, plus refunds reaching
  `processing`/`refunded`/`failed` and any chargeback. **Not fired for `open`** — the create
  response is how we learn about that one.
- **Mollie cannot re-trigger a webhook on request.** There is no "resend" button anywhere. That
  is the whole justification for the manual `…/payment-intents/{id}/sync` button and for the
  hourly reconcile cron: if a delivery is lost after the retries run out, the only way back is
  for us to ask.

`references_in_webhook` also accepts a **JSON** body defensively — an operator who points
Mollie's newer webhooks at this URL should get the id read out of the payload rather than
silence — but it takes **only the id** from it. A status in a JSON body is ignored on purpose.

### Next-gen webhooks are a different system

Mollie's newer, dashboard-configured webhooks sign the request with an `X-Mollie-Signature`
HMAC-SHA256 over the raw body and post a full entity snapshot. **We do not subscribe to them.**
`MolliePaymentProvider.verify_webhook` returns `True` — accept — and is precisely where that
signature would be checked. Doing so needs a shared secret from Mollie's dashboard, which is a
*second credential* to store, rotate and redact, and therefore a different issue.

Even if we did subscribe, the re-fetch would stay. A signature proves who sent a message, not
that the message is still true (`docs/PAYMENTS.md` §4).

## 7. Statuses, expiry, and the deployment note

| Mollie status | ours | means | webhook |
|---|---|---|---|
| `open` | `open` | created, nothing has happened | no |
| `pending` | `pending` | started, waiting on the bank | no |
| `authorized` | `authorized` | **held, not captured** (cards, Klarna, Billie, Riverty) | yes |
| `paid` | `paid` | definitive success | yes |
| `canceled` | `canceled` | the payer cancelled — definitive | yes |
| `expired` | `expired` | abandoned — definitive | yes |
| `failed` | `failed` | definitive failure | yes |

`authorized` is deliberately **not** treated as paid: the money is held and can still be
released, and booking it would credit an invoice against funds that have not moved. Only `paid`
sets `settled` on the seam's `PaymentStatus`, and only `settled` writes a ledger row.

`cancelled` (two Ls) is accepted as a synonym defensively — Mollie spells it with one — rather
than letting a cancelled payment read as "still open" forever. Any status the map does **not**
know is logged loudly and treated as `pending`: nothing settles, and the cron keeps asking.

**Never predict expiry locally.** It differs per method — iDEAL 15 minutes, cards 30 minutes,
Bancontact 1 hour, PayPal 6 hours, Klarna 48 hours, bank transfer 12 (+2) days — and it is
Mollie's to decide. Nothing here computes an expiry; the status comes from Mollie or it does not
come at all. That is also why the reconcile cron's 7-day horizon is a *bound on work*, not a
belief about when a payment dies.

### The callback path must be publicly reachable

Repeated here because it is the one deployment mistake this integration can make, and it fails
silently. Behind Cloudflare Zero Trust (`infra/compose.tunnel.yaml`, `docs/DEPLOY.md`) an Access
policy in front of the hostname will challenge Mollie's POST, Mollie will see a login page, retry
ten times over 26 hours and give up: **payments collected, never booked**. The path needs a
bypass rule. `MollieAccountRead.webhook_url` puts the exact URL on the settings screen for
exactly this reason — an admin who cannot see it cannot allow it. Full reasoning, and why the
endpoint is safe to expose, in `docs/PAYMENTS.md` §10.

## 8. Test mode

A `test_…` key creates payments in a **fully isolated dataset**. The checkout URL becomes a test
screen where any final status can be forced, and **webhooks fire identically** — which is what
makes the whole loop verifiable without money.

**Test payments settle nothing here**, on purpose. The intent reaches `paid`, `settled_at` stays
`NULL`, no `InvoicePayment` row is written, and the screen says *"testbetaling: niet geboekt"*.
`docs/PAYMENTS.md` §6 has the reasoning; the short version is that an agency who leaves a test
key connected gets an obviously-stuck screen rather than silently wrong revenue.

What Mollie's test mode offers for exercising the failure paths:

- **Magic amounts** trigger card failure reasons: €1001.00 → `invalid_card_number`, through to
  €1011.00 → `card_declined`. This is how to populate a real `details.failureMessage` for
  checklist item 6.
- **Test cards**: Amex `3782 822463 10005`, Mastercard `2223 0000 1047 9399`, VISA
  `4543 4740 0224 9996`.
- **Only EUR** is supported in test mode. A tenant invoicing in another currency cannot rehearse
  in it.
- `_links.changePaymentState.href` appears on test-mode payments and is how a status is forced
  from the API rather than the screen.

## 9. Permissions (CLAUDE.md §15)

**One key: `mollie.settings.manage`.** Admin-only by default and **never `client`**. It governs
the credential — adding, rotating, verifying, removing — and reading the settings screen. It is
also `MollieAccount.__activity_read_permission__`, so the credential's audit trail is readable by
exactly the people who may change it.

There is deliberately **no `mollie.payment.*`**. Starting a payment is an *invoice* act and
declares `invoicing.payment.link` — the key that already knows about documents, the company
horizon and the client role. Minting a parallel key here would mean an agency granting two
permissions to let a bookkeeper do one thing, and three the day a second provider ships. §6's
rule about not importing another module's internals is about code; this is the same rule about
grants.

`mollie_accounts` is **org-wide configuration with no client of its own** — no `company_id`, and
therefore no `__company_horizon_clause__` — which is exactly what §15 describes for a config
surface: the admin-only manage permission is what keeps a member out of it.

Every route under `/api/v1/mollie` declares the permission (deny-by-default; a route declaring
neither it nor an explicit `no_permission_required` is a build break). The **callback** is not
one of them: it lives on invoicing's router, serves every provider, and is exempted in
`tests/test_rbac_deny_by_default.py` alongside the Google Calendar webhook, for the same reason
and with the same reasoning written next to it.

## 10. Errors

`message` in the error envelope is always an i18n key (CLAUDE.md §9), so Mollie's own text never
goes in it — it is not translatable. Where the operation still commits (verify, and a failed
reconcile) the redacted text is persisted to the row's `last_error`, truncated to 500, and that
is what the settings screen and the invoice's payment panel render.

Mollie's error body is `{status, title, detail, field?, _links.documentation}`; `_message` takes
`detail` and falls back to `title`. A `422` carries `field`, which is stored as the seam's
`code`.

| our key | when | status |
|---|---|---|
| `errors.mollie.not_an_api_key` | the pasted secret does not start `test_`/`live_` | 422 |
| `errors.mollie_credential_unreadable` | the stored key will not decrypt — re-enter it | 409 |
| `errors.invoicing.payment_credential_rejected` | Mollie answered 401 or 403 | 409 |
| `errors.invoicing.payment_provider_unreachable` | no HTTP status at all: transport, timeout, an unparseable or over-size body | 502 |
| `errors.invoicing.payment_provider_failed` | Mollie answered and refused | 502 |
| `errors.invoicing.payment_no_account` / `_account_ambiguous` / `_account_inactive` | nothing to charge with, or nothing that picks itself | 409 |
| `errors.invoicing.payment_not_payable` | the invoice is not open, or nothing is outstanding | 409 |

Three outcomes, because they need three different buttons: **the credential is wrong** (only the
tenant can fix it — do not retry a 401), **we could not reach them** (try again in a moment), or
**they refused this particular request** (read the row's `last_error`). A `429` is a rate limit
and is retried once on a read; it is never retried on a create.

**A failed reconcile is recorded, not raised.** Its caller is a webhook or a cron, and raising
would roll back the very row that says we tried — a provider outage would then leave no trace at
all. `_note_error` writes `last_error` and `synced_at`, `settled_at` stays `NULL`, and the next
pass picks it up. Same contract as `oxxa`'s refused push, for the same mechanical reason
(`docs/OXXA.md` §10).

### Redaction

`client.redact` blanks anything matching `(test|live)_[A-Za-z0-9]{8,}` in a string, and it is
applied to any provider text before it is raised, logged or stored. Mollie authenticates with a
**header**, not a query parameter, so this is belt to the header's braces — the standing hazard
`docs/OXXA.md` §2 documents (httpx logging `str(request.url)` at INFO) does not leak a Mollie
key. It could still arrive inside an error body, a misconfigured proxy's response, or a future
endpoint's echo, and the cost of being wrong once is a live payment credential in a container
log that is shipped, retained and read by people who are not the tenant.

The stored `payload` on an intent is an **allowlist** — `id`, `mode`, `status`, `method`,
`amount`, the timestamps, `profileId` — and deliberately not `details`, which can carry the
payer's IBAN and name. A JSONB column nobody prunes is the wrong place for a third party's
personal data, and it would travel into every activity export.

## 11. Testing, and what is not here

`client.set_transport` is the **only** network seam: one module-level `httpx.AsyncBaseTransport`
that every client is constructed with. Left unset, a test that forgot to stub fails loudly on
connect instead of quietly reaching `api.mollie.com` — the same arrangement as
`tests/cloudflare_fake.py` and `tests/oxxa_fake.py`.

`tests/mollie_fake.py` is the stand-in: stateful, holding payments in a dict, answering
`POST /payments`, `GET /payments/{id}` and `GET /methods` from them, and able to move a payment
to any final status so the callback path is driven end to end. Four things it does on purpose,
each guarding a hazard rather than a behaviour:

- It offers **no way to hand a status to the callback** — a test settles an invoice by moving
  the *fake's* state and letting the re-fetch find it. A fake that read a status out of the
  request body would have quietly stopped testing the property the whole design rests on.
- It **records no headers**, only method, path and JSON body, so the credential cannot end up
  in a pytest failure dump. `test_the_fake_never_records_the_api_key` asserts it.
- It derives `mode` from the Bearer prefix exactly as `mode_of` does, because `mode` is what
  decides whether a settle writes a ledger row at all — a fake that always said `live` would
  make the test-mode dead end untestable.
- Every refund and chargeback path is in `FORBIDDEN_FRAGMENTS` and raises `AssertionError`
  *before* the credential gate and before any scripted failure, so no test setup can turn "we
  tried to refund somebody" into a tidy reportable error.

`tests/test_mollie_api.py` covers the credential (never echoed, verify-persists-through-failure,
mode-follows-the-key, rotation moves the callback URL, tenant isolation);
`tests/test_invoicing_payments.py` covers the money (outstanding not total, triple delivery
writes one row, the re-fetch beats the body, a forged token, test mode, the reconcile cron, the
query budget).

**What a suite against that fake does and does not prove.** The fake is written from the same
document as the parser, so it proves the parser agrees with the document — not with Mollie. Every
item on §1's checklist is a place where those two can differ and no test can tell. When a
credential arrives, capture real responses and re-cut the fake from them; that is the change that
turns a consistency check into evidence.

Genuinely not here, each a deliberate line rather than an oversight:

- **Refunds.** Not on the seam at all (`docs/PAYMENTS.md` §12): a refund moves money the other
  way and is not reversible. Mollie's Create-refund endpoint is documented and unused.
- **Mandates, recurring payments and subscriptions at Mollie.** The subscriptions module (#30)
  raises invoices; collecting them is one invoice at a time. A stored authorisation to charge a
  client is a materially different security posture and belongs in its own issue.
- **Payment Links, and the Profiles API** (§4, §2).
- **Next-gen webhooks** (§6) — a second credential, therefore a second issue.
- **A company-hub panel.** A Mollie account is org-wide configuration, so the whole working
  surface is Instellingen → Mollie plus the payment panel on an *invoice*, where `docs/UX.md`
  principle 6 puts each. The module contributes no nav item, for the reason `cloudflare` and
  `oxxa` contribute none: a payment provider is not a place you go.
- **A second provider.** `app/core/payments/` exists so Stripe or Adyen costs a package rather
  than a refactor, and `docs/PAYMENTS.md` §11 is the checklist. `known_payment_providers()`
  returns exactly one key today.
