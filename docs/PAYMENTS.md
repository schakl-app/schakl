# Online payments — the provider seam

> `app/core/payments/` (epic #269, issue #267): what schakl may ask **any** payment provider,
> how an unauthenticated callback names its tenant, and what invoicing does with the answer.
> Read this before changing anything under `apps/api/app/core/payments/` or
> `apps/api/app/modules/invoicing/payments.py`.

`docs/MOLLIE.md` is the first implementation and the only one today. Everything here is written
so that the second one is a new package rather than a refactor — which is the whole point, and
was not the original plan (§1).

## 1. The abstraction the issue argued against

**Issue #267 asked for a Mollie integration and explicitly argued against a seam.** The
reasoning was decent: Mollie already aggregates the methods an NL/EU agency needs — iDEAL,
Bancontact, cards, SEPA, PayPal — so "support more payment methods" is Mollie's problem and not
an architecture problem here. A `PaymentProvider` protocol with exactly one implementation is a
hypothesis, and the repository already carries one of those (`app/core/registrar/`,
`docs/OXXA.md` §11).

**The owner reversed it**, and the reversal is worth recording because the arguments are not
symmetric. The issue was right about *methods* and wrong about *providers*:

- An agency with US or UK clients asks for **Stripe**, and an agency with enterprise clients
  asks for **Adyen**. Neither is exotic and neither is a Mollie payment method.
- The cost of the seam is **one file today** (`backend.py`, plus a registry in `accounts.py`
  that would otherwise be an import). The cost of not having it is a rewrite of the settle
  path — the part that touches money — at the moment a second provider arrives, which is also
  the moment there is a live tenant depending on the first one.
- The three things a second provider actually differs on are all *behavioural*, and each has a
  wrong default that only shows up under the second implementation: whether the callback body
  may be believed, whether it is signed, and whether the create call is replayable. A codebase
  written straight to Mollie's shape would have hardcoded "no signature, re-fetch always" as an
  assumption rather than as a decision; one written straight to Stripe's would have hardcoded
  the opposite and been unsafe for Mollie. Writing both down is what `verify_webhook` and
  `references_in_webhook` are for.

The same trade the registrar seam took in #296 and the accounting seam took in #31. What is
deliberately **not** abstracted is the ledger: a confirmed payment writes an ordinary
`InvoicePayment` row (§3), so `invoicing` stays the single answer to "what has been paid".

## 2. Three layers, and why each boundary is where it is

```
app/core/payments/            vocabulary + callback addressing   ← names no provider
  backend.py                    PaymentProvider, PaymentRequest, PaymentSnapshot, PaymentStatus
  accounts.py                   PaymentAccount + the per-provider account resolver registry
  tokens.py                     {org}.{account}.{secret}
app/modules/mollie/           one provider's protocol            ← knows Mollie, knows no invoice
app/modules/invoicing/        what a payment *means*             ← knows invoices, names no provider
  payments.py                   InvoicePaymentService + handle_webhook
```

**Core owns the vocabulary because both of the others need it and neither may import the
other** (CLAUDE.md §6). The module that *collects* the money may not import the module that
*asks* for it, in either direction: `invoicing` cannot read `mollie_accounts`, and `mollie`
cannot read an invoice. Put the protocol in `invoicing` and every provider module imports a
domain module; put it in `mollie` and the second provider imports the first.

**A module owns one provider's protocol** — its HTTP client, its error mapping, its credential
table, its settings screen. `mollie/` holds *a credential and a conversation* and nothing else:
there is no Mollie-side mirror of a payment there, because "what has been paid" is invoicing's
question and a second copy of it in every provider module is how two screens start disagreeing.

**Invoicing owns the meaning.** A `PaymentSnapshot` is an observation; deciding that a
particular observation credits a particular invoice on a particular calendar day — and storing
the decision *beside* the observation, so "the provider says paid and we never booked it" is
expressible at all — is a domain act. It happens in `InvoicePaymentService`, in invoicing's own
tables, through the same `_settle` a bookkeeper's manual entry goes through.

The word "Mollie" appears in `app/modules/invoicing/payments.py` exactly once, in a sentence
saying so. That is the check: if a provider name has to be spelled in invoicing or in core to
make something work, the seam is missing a concept.

### How a credential is found without naming a provider

`accounts.py` is `app/core/registrar/presence.py` applied to a **row** instead of to a SQL
predicate. Each provider module registers one async resolver for its own table
(`register_payment_accounts("mollie", resolve_accounts)`); core only composes:

```python
accounts = await available_accounts(session, org.id)      # every provider, enabled modules only
account  = await resolve_account(session, org.id, "mollie", account_id)
```

Two properties are load-bearing.

**The resolver hands back the webhook secret, and core does the comparing.** A payment provider
posts no tenant hostname and no session, so resolution has to happen *before* anything is
scoped — which means the secret travels out to the verifier rather than the verification
travelling into the module.

**Connecting is lazy.** `PaymentAccount.connect` is a zero-argument callable, so listing the
accounts for a picker never decrypts a credential, and a decryption failure surfaces at the one
call site that can explain it (a rotated `SCHAKL_ENCRYPTION_KEY` means *re-enter the key*, not
*retry*).

`PaymentAccount` carries exactly what core is allowed to know: `provider`, `id`, `org_id`,
`label`, `mode`, `active`, `webhook_secret`, `connect`. An **inactive** account is still
resolvable on purpose — a callback for a payment it already created must still settle — it
simply cannot start new ones. And `available_accounts` returns inactive rows too, because the
list is the honest answer to "what is connected" and hiding a disabled account makes *"why can
I not pay?"* unanswerable from the screen.

### Nothing ever picks a credential for you

`InvoicePaymentService._pick_account` refuses to guess, which is `docs/OXXA.md` §10's rule
about registrar accounts applied to money. An agency legitimately holds two credentials — two
profiles mid-merger — and charging the wrong one is not recoverable by editing a row afterwards.
So: no active account is `errors.invoicing.payment_no_account` (409), more than one is
`errors.invoicing.payment_account_ambiguous` (409, with `fields: {account_id}` so the form knows
what to ask for), and exactly one is used without a prompt.

**With one tiebreak, and only one: a live credential beats a test one.** An agency integrating
holds both at once — that is the whole reason the credential is a row rather than a settings
singleton — and it is not a real ambiguity, because a test key collects nothing and settles
nothing (§6). It was never a candidate for a client's money, so preferring the live one is not a
judgement being made on anyone's behalf. Refusing there would also have refused a **client** in
the portal, who cannot read the account list at all (§8 keeps it at `:any`) and would have been
handed *"choose which one"* with nothing to choose from — #253's broken control, in front of the
one screen this feature exists for.

Two *live* credentials stay ambiguous, deliberately: that one has no principled answer, and the
agency resolves it by switching one off in Instellingen → Mollie.

### The callback token

`{org_id}.{account_id}.{secret}`, minted in `tokens.py`. It is deliberately the **same shape**
as the Google Calendar channel token (`docs/GOOGLE.md`) — reused rather than reinvented,
because the problem is identical and the failure mode of getting it wrong is a cross-tenant
write.

- **The org travels in the token, so nothing is ever read unscoped.** The obvious alternative —
  look the provider's payment id up across every tenant and see whose it is — is a second
  unscoped crossing (CLAUDE.md §5 sanctions exactly one, `core/instance/repo.py`) and it
  answers *before* authenticating, which is backwards.
- **The secret is compared, not merely present.** `org_id` and `account_id` are guessable in
  principle; a `secrets.token_urlsafe(24)` is not, and `matches()` compares it with
  `hmac.compare_digest`. A wrong secret is a bare **404** — never 401 or 403, which would
  confirm that the account exists.
- **It is per account, so rotating a credential rotates the URL.** A key is usually rotated
  *because* it leaked; leaving the old callback URL answering would keep one half of a
  compromised pair alive.
- **`parse()` is total.** It parses attacker-controlled input on a public endpoint, so it
  raises nothing and logs nothing, and `maxsplit=2` keeps a secret containing a dot intact.

## 3. The intent table, and why it is not `ExternalRef`

`invoice_payment_intents` is one row per **attempt**: `(org_id, invoice_id, provider,
account_id, external_id, status, amount, currency, mode, checkout_url, method, synced_at,
settled_at, last_error, payload)`. The unique key is `(org_id, provider, external_id)` — the
**provider's** payment, not the invoice.

The repository already has a generic external-reference table, and using it here would have
been wrong in a way that only shows up under load. `external_refs` is unique on
`(org, provider, local_type, local_id)` — *one row per local record*. An invoice legitimately
collects several attempts: iDEAL expires in fifteen minutes, cards in thirty, and clients
abandon checkouts and come back. Under `ExternalRef` the second checkout would overwrite the
first's external id, and a late callback for the abandoned attempt would then resolve to — and
settle against — a payment nobody made.

Other decisions the columns encode:

- **`amount` is frozen at creation** and is always the invoice's *outstanding*, recomputed
  then. Never `total`, and never a number the caller sent: `InvoicePaymentIntentCreate` carries
  no amount field at all. A partial payment registered in the meantime must not silently change
  what a checkout link already promised, which is why `_reusable` matches on the amount as well
  as the account — a stale link promising the old figure is worse than a new one.
- **`status` is the provider's own word, and `settled_at` is ours.** They answer different
  questions: one says what happened at the provider, the other says what we did about it.
  Collapsing them into a single invoicing status is exactly how *"the client paid and we never
  booked it"* becomes invisible — `docs/CLOUDFLARE.md`'s decided/observed rule (CLAUDE.md §10),
  applied to money. `status="paid"` with `settled_at IS NULL` is a real, reachable, repairable
  state, and both the screen and the reconcile cron key off it.
- **`account_id` is a bare UUID, not an FK.** The credential lives in the provider's own module
  and invoicing may not know its table (CLAUDE.md §6). Deleting an account therefore leaves its
  intents standing — they are invoicing's rows, they carry the ledger link that already settled, and
  deleting the history of how an invoice was paid because somebody rotated a key would be the
  wrong kind of tidy.
- **`checkout_url` is cleared once the status is final.** A link that answers "this payment has
  expired" is worse than no link.
- **`payload` is an allowlist of the provider's answer**, not the whole object — a provider's
  payment detail can carry the payer's IBAN and name, and a JSONB column nobody prunes is the
  wrong place for a third party's personal data.
- **`__company_horizon_clause__`** — the row's client is its *invoice's*, so it declares one
  (#285 failure mode 1). Every read in `payments.py` already goes through an invoice the
  document repository narrowed first, so this is the second lock rather than the first, which
  is exactly the arrangement #285 asks for: the next read added will not remember.

### A confirmed payment is an ordinary payment

This is the rule the whole design hangs from. `_settle` writes an ordinary `InvoicePayment` row
— the same row a bookkeeper creates when they see a bank transfer land — with
`method="online"`, `note="<provider>:<external_id>"` and `intent_id` set, and then calls
invoicing's own `_settle`, which recomputes `paid_total`, flips the status and emits
`invoice.paid`. Every consumer of that answer — the dashboard, the reminders cron, the
accounting export, the company panel — needed **no change at all**, and none of them knows a
provider was involved.

`method="online"` is one value for every provider on purpose: the *provider* is on the intent,
and a ledger row's method answers the bookkeeper's question (*how did this arrive?*), which is
"online", not "which vendor". `PaymentWrite.method` stays a closed `Literal["bank", "cash",
"card", "other"]` that does not include it — nobody should be able to hand-register a payment
as though a provider had confirmed it.

Two guards inside `_settle`, both of which report rather than throw:

- The **invoice is gone** (only a race can get there; the FK cascades).
- The **invoice was cancelled between checkout and settlement.** The client's money still
  moved, so this is recorded on `last_error` and left retryable — never a 409 thrown at a
  provider that would simply keep retrying, and never silently dropped.

`paid_on` is a calendar day and `paid_at` is an instant, so the conversion goes through
`org_zoneinfo` (CLAUDE.md §8). A payment that lands at 23:40 UTC is booked on the tenant's
tomorrow in Amsterdam, and on the server's today only by coincidence.

## 4. The callback: five gates, in this order

`POST /api/v1/invoicing/payments/webhook/{provider}/{token}` is unauthenticated, declares
`no_permission_required(...)` with its reasoning, and is `license_exempt` (§8). The logic lives
in `handle_webhook()` rather than in the route, so the security order is written once in a
function that can be tested without a transport. It returns a bare status and no body.

1. **The token names the tenant.** No hostname, no session, no unscoped lookup — the org comes
   out of a URL we minted. A malformed token, an unknown provider key (its module is disabled),
   or an org that is missing or not `ACTIVE`: **404**.
2. **The RLS GUC is bound before anything is read** (`set_current_org`). Every read below is
   org-scoped and fails closed, which is what makes step 3 safe to run against attacker-chosen
   ids.
3. **The secret is compared in constant time.** No such account for this org, or a mismatched
   secret: **404** — never 401 or 403, which would confirm the account exists.
4. **The provider gets its optional signature check**, now that the credential is in hand and
   *only* now: the secret is per tenant, so this cannot run earlier. `verify_webhook` returning
   `False` is a **404** with no state touched. (A credential that will not decrypt is **503**
   instead: that is our configuration problem, not the provider's, and 503 keeps the callback in
   their retry queue while an operator re-enters the key.)
5. **The body's ids are looked up, and nothing else in it is read.** `references_in_webhook`
   returns provider payment ids and only ids; status, amount, method and paid-at all come from
   `fetch_payment` — an authenticated call to the provider with the tenant's own credential.

**A webhook body is a hint, never a fact — the authenticated re-fetch is the authentication.**
Mollie's design states this outright and posts no status at all, so there is nothing to forge.
But the rule is not Mollie-specific and must survive a provider that posts a whole signed event:
a signature proves *who sent a message*, not *that the message is still true*. Between a
provider emitting `paid` and us processing it, a payment can be reversed; the re-fetch is what
makes the answer current as well as authentic. `verify_webhook` is an **extra** gate, never
*the* gate.

An id this tenant does not know answers **200**, deliberately: a provider must not be able to
learn which references exist here by watching status codes, and it is Mollie's documented
expectation. An empty parse also answers 200 — a malformed body is noise on a public endpoint,
not an exception. Anything we might recover from answers **503**, so the provider's own retry
schedule becomes the recovery mechanism rather than something merely tolerated.

| outcome | status |
|---|---|
| processed, or the reference is unknown here, or the body named nothing | `200` |
| malformed token · unknown provider · unknown/inactive org · wrong secret · failed signature | `404` |
| credential unreadable · anything raised while reconciling (rolled back) | `503` |

## 5. Idempotency: a row lock, and an index behind it

A provider retries a callback until it gets a 200 — Mollie ten times over 26 hours — and two
deliveries can be in flight at the same moment. The reconcile cron (§7) can also be running.
So there are two mechanisms and they are not redundant:

- **`SELECT … FOR UPDATE` on the intent**, taken first thing in `InvoicePaymentService.apply`.
  Two deliveries land as two transactions; the second waits there and then reads a `settled_at`
  the first has already written, and returns without settling again.
- **A partial unique index on `invoice_payments (org_id, intent_id) WHERE intent_id IS NOT
  NULL`.** Partial because a hand-registered bank transfer has no intent, and a hundred of
  those must not contend over `NULL`.

**An idempotency guarantee that lives in application code loses the race the database would
have won.** The naive version of this is a check — *"has this intent settled yet?"* — followed
by an insert, and the window between them is exactly where the second delivery lands. It is a
few microseconds wide and it is entered by every retry a provider makes; at scale, "rarely" is
a schedule. Losing it means charging a client twice and then having the conversation about a
refund. The lock is what makes the common case correct and cheap; the index is what makes the
uncommon case *impossible* rather than unlikely, including across two API replicas that share
no memory (CLAUDE.md §11's rolling deploy).

The same pair is what makes the callback and the cron safe to run against each other: they call
the same `reconcile` → `apply` path, and neither needs to know the other exists.

## 6. Test mode is a deliberate dead end

An intent whose `mode` is `test` follows the entire loop — create, redirect, callback,
authenticated re-fetch, status update, screen — and then **stops**. `settled_at` stays `NULL`,
no `InvoicePayment` row is written, and the invoice does not move.

That is the point. The whole mechanism is observable end to end, and the one step withheld is
the one that would book a real invoice as paid against money that does not exist. An agency that
leaves a test key connected — or that connects a test key first, which is what everyone does —
gets an obviously-stuck screen (*"testbetaling: niet geboekt"*) instead of silently wrong
revenue in their accounting export.

`mode` is stored on the intent, not read from the account at settle time, so a key rotated from
test to live afterwards cannot retroactively make an old test payment real.

## 7. The hourly reconcile is the safety net

`invoicing_payments_reconcile`, an ARQ cron on the invoicing module descriptor, at **:25 past
every hour** (off the hour so it does not pile onto everything else scheduled at `:00`), bound
per org through `run_per_org`.

The callback is the fast path; this is what makes it **safe to miss**. A webhook can be lost for
entirely ordinary reasons — an access proxy in front of the API (§10), a redeploy, a firewall
rule, a DNS blip — and the failure is invisible from the outside: the client's money moved and
the invoice still says open. Nobody gets an alert, because nothing errored.

- **Hourly, not daily.** An agency chasing *"the client says they paid"* should not have to
  wait out a nightly job, and Mollie's own retries run for 26 hours — an hourly pass converges
  well inside that.
- **Cheap when nothing is in flight**: one `available_accounts` call per org, and it returns
  immediately for an org with no provider connected. Then one query for unsettled intents.
- **Bounded**: intents created within the last 7 days, at most 100 per org per pass, oldest
  first, logging a warning when it is truncating. An unbounded read is a build break
  (CLAUDE.md §9).
- **`paid` is not in the skip list.** `failed`, `expired` and `canceled` are — nothing more will
  happen to them. A `paid` intent with no `settled_at` is precisely the case this cron exists to
  repair: the money arrived and the ledger write did not happen (a cancelled invoice at the
  time, a crash mid-settle).
- **A disconnected credential is recorded, not retried forever.** No account for
  `(provider, account_id)` writes `credential unavailable` to `last_error` and moves on.

Plus a **manual** repair: `POST /invoices/{id}/payment-intents/{intent_id}/sync`, gated on
`invoicing.payment.link:any`. #267 asked that sync failures be "surfaced and retryable, not
silently dropped", and that needs a button as well as a cron — an operator who has just fixed
the Zero Trust rule should be able to settle the payment without waiting for the next pass. It
is `:any` and not the floor precisely because it spends an outbound provider call on every
press: a client has no use for it, and leaving it at the floor would have put a rate-costed
external call behind a button on a client-reachable page.

## 8. Permissions (CLAUDE.md §15)

`invoicing.payment.link` — **scoped**, `ROLE_ADMIN` by default at `:any`, `ROLE_CLIENT` by
default at `:own`.

It is a new key rather than a reuse of `invoicing.payment.write`, and the distinction is the
whole reason a client may hold it. `payment.write` says *"this money arrived"* and is a
bookkeeping claim; `payment.link` says *"open a checkout for what is owed"* and settles nothing
on its own — the provider's own authenticated answer does that, through a callback nobody can
forge. A client paying their own invoice needs exactly this and nothing more; reusing
`payment.write` would have handed them the ability to declare an invoice paid.

Scoped for #266's reason: reads and writes on a module cluster, and `:own`/`:any` is the only
thing that can fence surfaces a company horizon cannot narrow.

| route | permission |
|---|---|
| `GET /invoicing/payment-accounts` | `invoicing.payment.link:any` — org-wide configuration; no client's row could be narrowed to it |
| `POST /invoicing/invoices/{id}/payment-intents` | `invoicing.payment.link` (floor — a client holds `:own`) |
| `GET /invoicing/invoices/{id}/payment-intents` | `invoicing.invoice.read` (floor — a client must see the state of the payment they just made) |
| `POST …/payment-intents/{intent_id}/sync` | `invoicing.payment.link:any` (§7) |
| `POST /invoicing/payments/webhook/{provider}/{token}` | `no_permission_required(...)` + `license_exempt(...)` |

What the portal reads instead of the account list is **`InvoiceRead.online_payment`** — a
boolean derived on the detail read: this org has an *active* credential **and** this document is
open with something outstanding. It answers the only question a payer has (*can I pay this
here?*) without naming an account, and it exists because a control that always refuses is a
broken control (#253). `InvoiceRead.intents` rides the same detail read, in one grouped query
for the whole batch (`docs/PERFORMANCE.md`).

Both new keys — `invoicing.payment.link` and each provider module's own settings key — are
brand new, so the ordinary startup reconciler grants them to each org's system roles once
(`org_settings.applied_permission_defaults`). No `DefaultsRevision` is needed: that mechanism
exists for *widening an existing key's* defaults, which is invisible to a key-based diff.

**A licence expiry makes a module read-only; it does not make the agency's takings disappear.**
A provider module is licensed like every other outside connection, and past expiry + grace the
mount-time gate turns its mutations into 402 — no new credential can be connected and no key
rotated. The callback is the one route carrying `license_exempt`, and the reason generalises
past this feature: a 402 there would take a payment that has already left someone's bank
account and drop it on the floor, and no retry would ever fix it, because the provider's retries
would 402 too. Gate what the agency *does*; never gate the recording of what has already
happened to them. (Compare `docs/PORTAL.md`'s single exemption: ending your own impersonation,
for the same shape of reason.)

## 9. The link a payer follows, and the QR (#268)

Two URLs go out with every payment and they are not the same thing:

- **`return_url`** — where the provider sends the payer afterwards, whatever the outcome:
  `{org_base_url}/invoices/{invoice_id}`, the invoice's page in the client portal. Not a
  thank-you page, because "whatever the outcome" includes cancelled and failed; the live
  document is the honest landing.
- **`webhook_url`** — the callback of §4, carrying the token.

Both go through `app.core.hosts.org_base_url` like every other generated absolute link, so
neither can point at a host whose edge cannot serve it (Golden Rule 4 — no hardcoded domain).

The **QR block** an invoice may print (`render/qr.py`, block key `payment_qr`, off by default)
encodes that same portal URL — **not** a checkout URL, and the difference is the security
decision worth remembering. A live provider checkout URL is a **bearer credential**: printing
one on paper hands whoever picks that paper up the ability to look at, and settle, somebody
else's bill. The portal link goes through the login #193 already established, so a scan by the
right person lands on the invoice and a scan by anyone else lands on a sign-in screen. The cost
is a redirect through login, and that is the correct cost. It is drawn as an inline `<svg>`
because the document renderer's CSP allows `img-src data:` and nothing else and its Jinja
environment fetches nothing at all (`render/engine.py`), so an `<img src="https://…">` would be
blocked in the preview and blank in the PDF.

The caption is the only thing a connected provider changes: *"Scan om te betalen"* when one is
connected, *"Scan om deze factuur te bekijken"* when none is. The QR is not gated on a provider
— the code still works without one, it opens the invoice.

## 10. Deployment: the callback must be publicly reachable

**The one thing an operator has to get right, and the failure is silent.**

`POST /api/v1/invoicing/payments/webhook/{provider}/{token}` is called by the provider's
servers, with no session and no cookie. Behind **Cloudflare Zero Trust** — the
`infra/compose.tunnel.yaml` deployment in `docs/DEPLOY.md` — an Access policy in front of the
hostname will challenge that POST and the provider will see a login page. It will retry
(Mollie: ten times over 26 hours) and give up. **Payments are collected and never booked.**

So the deployment needs a **bypass rule** for that path — public, no Access policy — and only
that path. It is safe to expose because it authenticates itself twice over (§4): the token's
secret, and the authenticated re-fetch that is the actual source of truth. There is nothing to
read there; the endpoint returns a bare status and no body.

Two things make this survivable when it is missed:

- The **callback URL is shown on the settings screen** of each provider module, precisely so an
  admin can allow it. An admin who cannot see the URL cannot add the rule.
- The **hourly reconcile** (§7) picks the payment up anyway, within the hour. It is the reason a
  missed rule is a delay rather than an incident — but it is a *safety net*, not the mechanism,
  and an instance running permanently on it is spending an outbound provider call per unsettled
  intent per hour.

One more, from Mollie's own docs and true of any provider: **a 301/302 redirect drops the POST
body.** If anything in front of the API rewrites the callback URL — a trailing-slash redirect,
an http→https bump — it must use 307/308 or the callback arrives empty and parses to nothing.

## 11. Adding a second provider — the checklist

Everything below is a new package under `apps/api/app/modules/<provider>/`. Nothing in
`app/core/payments/` and nothing in `app/modules/invoicing/` should need to change; if it does,
that is the interesting part of the change and belongs in review.

**1. A credential table.** `<provider>_accounts`, org-scoped, RLS-forced, `AuditableMixin`.
A **row, not a settings singleton** — an agency mid-migration holds two, and an agency
integrating holds a live and a test credential at once. Secret Fernet-encrypted
(`app.core.crypto`, the `*_encrypted` convention), write-only through the API, a
`*_configured` boolean in the response and never the value. A `webhook_secret` column,
`NOT NULL`, regenerated whenever the credential is rotated. Rotation clears everything the old
credential vouched for, so a stale "verified" badge cannot speak for a secret nobody has tested.

**2. The five protocol methods** (`app.core.payments.PaymentProvider`):

| method | must |
|---|---|
| `verify()` | prove the credential and return a small fact dict for the settings screen. Raise `PaymentProviderAuthError` when rejected — its own class, because retrying cannot help |
| `create_payment(request)` | open a payment, return a `PaymentSnapshot` including `checkout_url`. Send an idempotency key derived from `request.reference` where the provider supports one, and **never retry blind** |
| `fetch_payment(reference)` | the authenticated truth about one payment. Return `None` — not an error — for an id this credential does not know |
| `references_in_webhook(body, headers)` | a **classmethod**: it runs before any credential is resolved. Return `[]` for anything unparseable. Read **only ids**, even from a body that carries the whole entity |
| `verify_webhook(body, headers)` | `True` where the provider does not sign; otherwise `hmac.compare_digest` against the per-tenant secret. Runs *after* the credential is resolved |

Amounts are `Decimal` in and out; the string↔decimal conversion happens in the adapter and a
float never appears on either side of it. Statuses map onto `PaymentStatus`, and **anything
unrecognised maps to a non-final state** (log it loudly) — the only dangerous guess is one that
settles an invoice. `AUTHORIZED` is deliberately not `PAID`: money held is not money captured.

**3. An account resolver** — `async (session, org_id) -> [PaymentAccount]`, reading through the
RLS-bound session it is handed, with `connect` as a lazy closure over the row.

**4. Self-registration** in the package `__init__`, beside the `ModuleDescriptor`:

```python
register_payment_provider(Provider.key, Provider)      # the class, not an instance
register_payment_accounts(Provider.key, resolve_accounts)
```

`key` is the module's own name. Registering a duplicate key raises rather than replacing — two
providers answering to one slug is unfixable at runtime, and here it would mean a callback
parsed by the wrong adapter.

**5. One permission**, `<provider>.settings.manage`, admin-only by default and **never
`client`**. There is deliberately no `<provider>.payment.*`: starting a payment is an invoice
act and declares `invoicing.payment.link`. Minting a parallel key per provider would mean an
agency granting two permissions to let a bookkeeper do one thing, and three once a third
provider ships.

**6. A settings screen** under Instellingen showing the connected credentials, their mode, a
verify button, the last error verbatim, and — not optional — **the callback URL** (§10).

**7. `sku=`, and the `LICENSE-COMMERCIAL.md` entries** for the API module, its web module and
its settings route, plus the per-directory `LICENSE` marker.

**8. Prefixed schema names.** `<Provider>AccountRead`, not `AccountRead`. Two components
sharing a name make FastAPI qualify **both** into
`app__modules__…__schemas__AccountRead`, so a collision silently rewrites the *other* module's
generated types and breaks its web callers on the next `gen:client` (`docs/OXXA.md` §11).

**9. A transport seam and a stateful fake.** One module-level `set_transport()` and nothing
else that reaches the network. Left unset, a test that forgot to stub must fail loudly on
connect rather than quietly reaching the real API.

**10. Both message catalogs**, `en.json` *and* `nl.json`, in the same change (Golden Rule 2).

What a new provider must **not** do: write to `invoice_payments` or `invoice_payment_intents`,
import anything from `app.modules.invoicing`, keep its own mirror of a payment's status, or add
a second webhook route. One callback route serves every provider, and the parsing is delegated
through the seam.

## 12. What is not here

- **Refunds.** There is deliberately no `refund` on the protocol. A refund moves money in the
  other direction and is not reversible; this seam is the collect-and-reconcile slice, and an
  issue that wants refunds should extend the protocol consciously rather than inherit the power
  by accident. Same reasoning as `RegistrarProvider`'s missing `create`/`transfer`/`renew`.
- **Recurring payments, mandates and subscriptions at the provider.** The subscriptions module
  (#30) raises invoices; collecting them is one invoice at a time today. Provider-side
  mandates are a materially different security posture (a stored authorisation to charge) and
  belong in their own issue.
- **A payment-link object that outlives a checkout.** Some providers have one; the intent row
  plus a fresh checkout covers the same ground with no second lifecycle to reconcile.
- **A payments *module*.** There is no `app/modules/payments/`. Payments attach to invoices,
  and an invoice is the thing a user opens; a separate list of payments detached from what they
  settle is a screen nobody asked for.
- **A second provider.** `app/core/payments/` exists so that costs a package rather than a
  refactor, but `known_payment_providers()` returns exactly one key today, and a seam with one
  implementation is a hypothesis. The second one is what tests it.
