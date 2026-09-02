# Invoicing — invoices & quotes (issue #207)

> The native billing suite: invoices and quotes raised inside the CRM, wired into time
> tracking and subscriptions, with tenant-configurable tax, templates, numbering and
> automatic payment reminders. Read this before touching `apps/api/app/modules/invoicing/`
> or the web module.

## The shape

Two documents, one engine. `invoices` and `quotes` are separate tables and endpoints (their
statuses and numbering differ) sharing the calculation (`calc.py`), the tax snapshots, the
templates and the rendering (`render/`). Everything is org-scoped + RLS-forced,
custom-fieldable (§13) and auditable (§16), like every module.

```
draft ──issue──▶ open ──nothing left outstanding──▶ paid
  │                │◀───payment removed────────────┘
  │delete          │cancel (no payments, not credited)
  ▼                ▼
gone           cancelled

credit note: draft ──issue──▶ open ──┬─ absorbed by its source ──▶ paid (settled)
                                     └─ refund registered ───────▶ paid (refunded)

quote: draft ──issue──▶ open ──▶ accepted ──convert──▶ (invoice draft)
                          │  └─▶ rejected                    │
                          └────▶ expired (cron, past validity)
                     accepted ◀─── deleting that draft reverts the quote
```

**Overdue is derived, never stored**: `open` + `due_date` before the org-local today **and
something still outstanding**. The list, the summary, the company panel and the reminders
cron all compute it the same way. **Credited is derived the same way**, from
`credited_total` — an invoice a credit note wrote off is not `paid` (nobody paid it, and
that would book it as revenue) and not `cancelled` (it was a real document); it is an open
invoice that owes nothing.

## The rules that bite

- **Clients send lines, never totals** (#48's rule applied to money). The service recomputes
  `subtotal`/`tax_total`/`total` from the lines on every write, in `Decimal`. The web's line
  editor shows a preview computed by `calc.ts` — a display mirror, never the authority.
- **Tax per rate group, rounded half-up once per group** (`calc.py`): all lines sharing a
  `(pct, category)` sum first, then tax, then one rounding — the shape UBL's `TaxSubtotal`
  models. Per-line rounding drifts cents on long invoices; it is deliberately not done.
  Inclusive prices (`prices_include_tax`) peel the tax out of the group gross so net + tax
  reconciles exactly to what the customer saw.
- **Snapshots over joins** (#64's rule): a line freezes `tax_rate_pct` + `tax_name` (in the
  document's locale) when written; a document freezes its `customer` bill-to block at issue.
  Re-rating a tax or moving a company never rewrites what a client was sent.
- **The bill-to is addressed to the client's *legal* name, and that is not what the app calls
  them.** `companies.name` is the label — what every list, picker, panel, report and
  notification prints — and `companies.legal_name` is the entity a document must be addressed
  to. `NULL` there means *the label is also the legal name*, which is the honest state of most
  clients and of every row that existed before the column, so the resolution is one rule stated
  once (`app/core/naming.document_name`) and an instance that upgrades and types nothing
  invoices exactly as it did. The snapshot carries **both**: `customer.name` is the resolved
  addressee, `customer.trade_name` the label. Two keys because two different readers want
  different halves — EN 16931 splits them too (`PartyName/Name` is BT-45's trading name,
  `PartyLegalEntity/RegistrationName` is BT-47's registered one, and both were fed the same
  string until the split), and the covering e-mail's `{company}` greets a human, so it reads
  `trade_name` and falls back to `name` for documents issued before any of this existed. It is
  called `trade_name` and not `label` because the renderer's own `customer_values` already has
  a `label` and there it means the *heading* over the block ("Aan:"); two keys one dict apart
  meaning different things is a bug with a long fuse. `_customer_snapshot` is the only builder
  — the subscription cron had grown a hand-written copy of the dict, and the two had already
  drifted (it omitted `client_number`), which is exactly how "which name does an invoice say?"
  comes to depend on who raised it.
- **Numbers allocate at issue, under a row lock.** Drafts have no number; issuing allocates
  from the per-org sequence on `invoicing_settings` (`SELECT … FOR UPDATE`), formatted by
  the tenant's `{year}`/`{yy}`/`{seq}`/`{seq:N}` template, optionally resetting each
  org-local year. A partial unique index (`org_id, number`) is the backstop; the allocator
  walks past collisions after a manual sequence rewind.
- **Issued money is immutable.** After `draft`, the money-bearing fields 409
  (`errors.invoicing.locked`); process fields (reference, notes, due date, template, locale,
  reminders pause, exchange rate) stay editable. Corrections are a **credit note**
  (`POST /invoices/{id}/credit`): a draft mirroring the invoice with negated prices,
  `credit_for_id` pointing home.
- **A credit note reaches the balance, not just the paperwork.** It used to be a document
  and nothing else, which left the invoice it corrected `open`, in arrears, and receiving
  dunning mail for money the client no longer owed — while the credit note itself, whose
  total is negative, could never satisfy `paid_total >= total` and so stayed open for good.
  Two counters mirror `paid_total` and fix both ends: **`credited_total`** (how much of this
  invoice issued credit notes wrote off) and **`applied_total`** (how much of *this* credit
  note its source absorbed). Outstanding is
  `total − paid_total − credited_total + applied_total`, in one place (`calc.outstanding_of`
  and `calc.OUTSTANDING_SQL`, so the hydrated row and the dashboard's raw SQL cannot drift).
- **Allocation happens once, at issue.** A draft credit note moves nothing — it is not a
  document yet and its lines are still editable, which is exactly how a *partial* credit
  works: edit the draft down before issuing. The source absorbs what it has room for; the
  remainder stays owing on the credit note. That one line is the whole difference between
  the two cases an agency has:
  - crediting an **open** invoice — the source has full room, so the note is fully applied
    and both documents come to rest without a cent moving;
  - crediting a **paid** one — no room, so the note absorbs nothing and stays open for the
    refund, which is registered as a **negative payment** on the credit note and settles it.
  Re-deriving the split on every read instead would let a later payment silently move what a
  credit note is recorded as having settled.
- **A full credit hands the work back.** Crediting is usually the prelude to re-billing
  correctly, and until #207's follow-up you could not: the invoice went on holding
  everything it billed, so the hours stayed stamped `invoiced_at` and the agreement's month
  stayed retired — invisible to the hours picker and to the cycle cron alike. Issuing a
  credit note that covers its source in full now releases that invoice's time entries,
  subscription periods and domain periods, exactly as `cancel` has since #207 and for the
  reason stated there. Keyed off the **documents** (`_credited_by_notes`), not off
  `credited_total`, so a credited *paid* invoice — which absorbs nothing — releases too.
  A **partial** credit releases nothing: it corrects an amount, and nothing on it says which
  hours or which month the corrected part was, so releasing all of them would put work back
  on offer that the standing part of the invoice still bills. Recorded as `work_released`.
  The corollary: a credit note that actually released something can no longer be
  **withdrawn** (`errors.invoicing.credit_released_work`) — the work is back on offer and may
  already sit on a new invoice, so re-claiming it cannot be done safely; bill it again
  instead. One that released nothing (an invoice of plain product lines) still withdraws, so
  the refusal costs only the case that earns it.
- **A credit note is never dunned, and never credited.** The reminders cron chases
  `outstanding > 0` (not `status = 'open'`) and skips credit notes outright — the renderer
  already guarantees a credit note never asks to be paid, so the dunning run must not
  contradict the document it would arrive next to. Crediting a credit note 409s
  (`errors.invoicing.already_credit_note`): a bookkeeper re-bills with an invoice.
- **Issued invoices don't delete — they cancel.** Delete is draft-only; cancel requires no
  registered payments and releases any billed time entries — and any claimed subscription
  periods, so cancelling never retires an agreement's month for good. It also refuses an
  invoice a credit note has written down (`errors.invoicing.has_credit_notes`), which would
  strand that note's allocation. A **credit note** cancels from `paid` as well as `open` —
  a fully applied one rests at `paid` without a cent having moved, and withdrawing it hands
  its `applied_total` back to the invoice it corrected. What may never be cancelled away is
  *registered money*, which is `paid_total`, not the status.
- **A line knows what it is** (`line_kind`: `hours` / `subscription` / `domain` / `product`).
  An agency's invoice mixes worked hours, recurring agreements, domain renewals and one-off
  sales, and the reader has to tell them apart — "24 uur × € 95", "Hosting maart" and
  "vlotr.nl 2026–2027" answer different questions. So the kind is stamped by *whoever builds
  the line* (`from_time` → hours, the cycle cron → subscription, the renewal cron → domain, a
  product pick or a hand-typed line → product) and travels to the document, which groups and
  subtotals by it. It is presentation and provenance, never money: totals are computed exactly
  as before. A document whose lines are all one kind gets **no** section headers — a lone
  "UREN" band subtotalling to the subtotal beneath it is noise; headers earn their place when
  two kinds must be told apart. A credit note and a quote conversion carry the source
  document's kinds over.
  **`domain` split out of `subscription` in #302**, reversing the earlier call that a renewal
  is just a recurring line and the distinction bought nothing. It buys one thing, and it is
  the thing an agency does every month: a register of forty renewals is reconciled line by
  line against the registrar's own invoice, and it has to be findable as a block rather than
  mixed in among three hosting retainers. Rows written before the split keep saying
  `subscription` — a kind is a snapshot (#64), so documents a client already read do not
  change shape underneath them. Every read path that has to treats the two as one legacy
  family; `_CLAIM_SOURCES.legacy_kinds` is where that is declared, and the domain source
  answers to **both** kinds precisely so the upgrade cannot re-enter the double-billing bug
  provenance was added to close.
- **A billed period is claimed, so the cron knows it is already paid**
  (`invoice_subscription_periods`, `invoice_domain_periods` — `invoice_time_entries` for
  agreements). One column on `invoices` holds one agreement and one period, while a
  hand-built invoice routinely carries three subscriptions, eleven renewals and some hours;
  so the claim moved to its own table, keyed `(org, source, period_end)`, and
  `on_subscription_due` / `on_domain_due` consult it before drafting. A second document
  claiming the same period is refused with `errors.invoicing.period_already_billed` rather
  than left to 500 on the unique index. The claim is rebuilt from the lines on every write:
  drop the line and the period goes back to the cron. The lookups on
  `invoices.subscription_id` / `.domain_id` stay as the backstop for rows the crons drafted
  before the tables existed.
- **A line records what it bills, because the claim tables could not say *which* line did.**
  `invoice_lines` carries `time_entry_ids` (a **list** — a grouped line covers a project's
  worth of entries), `subscription_id` / `domain_id` and the period. Without it the round
  trip was broken in a way no functional test would catch: the editor replaces lines
  wholesale on save, `LineRead` did not echo the claim, so **opening a draft the cron raised,
  changing one word and saving released the claim** — and the cron billed the month again.
  The same gap on the hours side meant `update` never linked or released a time entry at all,
  so removing an hours line left it stamped invoiced with no line billing it. Both halves are
  now reconciled from the stored lines on every write.
  A **legacy guard** keeps the upgrade from re-entering the bug: a document whose lines of a
  kind all carry no provenance is a pre-upgrade one, and its claims are left alone until
  someone edits the lines that hold them. The migration also attributes existing claims to
  their lines, unambiguously where an invoice holds exactly one, and by matching the
  `dd-mm-yyyy` period both the picker and the cron bake into the description where it holds
  several. Credit notes and quote conversions copy the kind and deliberately **not** the
  provenance: a correction claims nothing.

## Automatic invoicing is a level, not a switch (`AutoInvoiceMode`)

`app/core/billing.py` — core vocabulary, because `subscriptions` and `domains` each store an
agreement's override and put it on their `due` event, and `invoicing` resolves it against the
org default (`invoicing_settings.auto_invoice_mode`). Four levels, each containing the last:

| level | what the cron does |
|---|---|
| `off` | nothing. The period stays outstanding and the editor's picker offers it. |
| `draft` | a draft appears. **The default** — what every instance did before the level existed. |
| `issue` | the draft is also issued: number, bill-to freeze, due date. Nobody has seen it. |
| `send` | and it is e-mailed to the client with the PDF attached. |

`NULL` on an agreement means **inherit**, never *off* — §14's three-state discipline. Where
per-org config cannot express a per-agreement fact, the agreement wins: an agency automating
twelve hosting retainers still assembles by hand the one client whose invoice is argued over
every month, and "turn the feature off" is not an answer to that.

**`issue` and `send` overrule #31's original *"do not auto-finalise financial documents"***.
That was the right default and is still the shipped one; going further is an owner decision,
made explicitly, and the two steps it adds are the two a delete cannot undo — an issued
invoice is corrected by a credit note, and a sent one has been read. So:

- Each step **degrades to the previous one** rather than propagating. An org that cannot issue
  (no seller name) keeps its draft and gets one `auto_issue_failed` entry on the trail; the
  month's billing is worth more than the automation.
- **Sending is a separate pass** (`jobs.py`, `_send_auto_issued`), not part of the drafting
  handler. `run_per_org` gives a whole org one transaction, so mailing at draft time would let
  a later agreement's failure roll back an invoice whose e-mail had already reached the client.
  `auto_send_pending` is written in the drafting transaction and read by the next job, so
  nothing is ever mailed for an invoice that did not commit. A transient provider failure
  retries tomorrow; a structural one (no recipient, no transport) clears the flag and records
  `auto_send_failed` once, the reminders discipline exactly.
- **The cycle advances either way.** A period nobody drafted is not lost: it stays unclaimed,
  and that is precisely what the picker enumerates.

## The client reads their own invoices, and `invoice.read` is two permissions (#266)

A contact with a portal login (#193) can now open **My invoices**: their companies' issued
documents, the derived *overdue* status, and the same PDF the agency sends. Three separate
refusals hold it, and each answers a different question.

- **Which rows exist for them** is the company horizon, unchanged (#191/#252). `Invoice`
  carries `company_id`, so the tenant repository already filtered it; nothing was added.
- **Whether a draft exists at all** is `Invoice.__portal_horizon_clause__`, the contacts
  pattern (`Contact.__portal_horizon_clause__`, #193). `_DocumentService` hands an
  `is_portal` caller a repository that overrides `horizon_condition`, so the clause is the
  one answer *every* path takes — `get_or_404`, the list, its `total`, `for_company` behind
  the company panel, and therefore `/pdf`, `/preview` and `/ubl`, which all load through
  `get()`. Overriding `_scoped` instead would leave the others on the looser rule; that was
  #285. A draft answers **404, not 403**: a 403 confirms the agency is drafting something
  for them, which is the fact being withheld.
  It follows `ctx.is_portal` — *who is asking* — and deliberately not the scope below. A
  staff member restricted to one company group still sees that client's drafts, because
  drafting the invoice is their job.
- **Whether they reach the invoicing module at all** is the scope. `invoicing.invoice.read`
  is `("own", "any")` since #266, because one key gated seven endpoints and only three of
  them are documents. `:any` — declared as `require_permission(_READ, _MODULE)` in
  `router.py` — fences `/settings` (seller identity, IBAN, numbering, reminder policy),
  `/tax-rates`, `/products` (the agency's price list), `/templates`,
  `/invoices/{id}/refs` (accounting-sync bookkeeping) and `/uninvoiced` (the org-wide
  unbilled backlog, with every employee's name and hourly rate on it). None of those is a
  row a company horizon could narrow — there is no client whose price list this is — so the
  scope is the only thing that can fence them. `/summary` stays on the floor and zeroes what
  the caller may not know: the draft count, and the quote figures, whose query is skipped
  rather than merely blanked.

Quotes stay out: whether a client should watch an offer's status before accepting it is a
product decision nobody has made, and `invoicing.quote.read` stays staff-only. `Quote`
carries the mirror clause anyway — the role is freely editable in Instellingen → Rollen, and
the day a tenant ticks it the answer should already be "your own, never our drafts".

The clause also had to reach **`core/scope.py`'s `entity_visible`**, which answered with the
staff horizon. That gate is the only one `GET /files` has — it takes `(entity_type,
entity_id)` from the caller and declares `no_permission_required` — so a client held off a
draft everywhere else could still list the documents attached to it. Fixed in core, because
`directory.py` already had the rule and having it in one of two places is how they drift.

**Portal impersonation changed shape here too.** Giving `client` an invoice read means staff
signing in as a client could read that client's invoices — which `PermissionSet.covers` (#296)
stopped by refusing the whole session, locking out every `member` who cannot read invoices. It
now **caps** instead: an impersonated portal session runs as the target intersected with the
impersonator, so that member signs in and simply has no invoices, and the banner says the view
is narrowed. See `docs/IMPERSONATION.md` — the guard is unchanged in strength, only in shape.

**Existing orgs need a one-time nudge, and it is not a migration.** The startup reconciler
diffs `org_settings.applied_permission_defaults` by *key*, so widening an already-applied
key's `default_roles` changes nothing — and no per-role diff could tell *never offered* from
*offered and removed*, which is what makes "a tenant who unticked something keeps it
unticked" true. So a widening is recorded as what it is: a `DefaultsRevision` in
`app/core/permissions/reconcile.py` with its own `@rev:` marker in the same array. This one
rewrites the bare `invoicing.invoice.read` to `:any` wherever it is stored — every role
including tenant-authored ones, plus API-key scopes — and grants `client` the `:own` half.
The rewrite changes nobody's access (`PermissionSet.has` already answered `True` for a bare
key at every scope); it changes the *spelling*, because `validate_permissions` refuses to
store a scoped permission bare and would otherwise 422 the tenant's next save of a role that
was working fine.

## Tax is tenant data, locale-seeded (`taxseeds.py`)

`invoicing_tax_rates` is seeded once, for an **empty** catalog only, from the org's
`tax_country` (NL: 21% hoog / 9% laag / 0% nul / vrijgesteld / verlegd; BE/DE/FR/ES/IT/AT/
GB/CH/US sets; a generic fallback) — the `leave_holidays` discipline: **derived suggestions,
never law in code**. Tenants rename, re-rate, deactivate, delete and extend freely; nothing
they changed is ever resurrected. Categories (`standard/reduced/zero/exempt/reverse_charge`)
drive behaviour: exempt and reverse-charge groups charge nothing whatever their nominal pct,
and reverse charge prints its notice + codes `AE` in UBL. `ledger_code` is the mapping seam
for accounting packages.

## Deep links

- **Time (module `time`)**: "to invoice" = approved AND billable AND `invoiced_at IS NULL`
  (the time module's own definition). `POST /invoicing/invoices/from-time` builds a draft
  (grouped per project / day / entry; rate = the request's override, else the logger's
  effective employee rate (#226: personal → leave org default), else the invoicing org
  default — grouped lines split per rate), stamps `invoiced_at` through the published column, and remembers
  exactly which entries in `invoice_time_entries` — so deleting/cancelling the draft un-bills
  exactly those and nothing else. An entry can be on one invoice, ever (unique constraint),
  and each built line records the entries it covers, so an edit of that draft releases exactly
  the hours whose line went. `GET /invoicing/unbilled` is the capped, counted list behind the
  Uren picker — the cap bounds the *detail*; the count and the money come from an aggregate
  over the whole set, so "12 uren nog te factureren" is never a truncated number.
  **Where `billable` comes from (#284)**: the API resolves it, not the browser. A create or
  timer-start that omits the flag inherits the project's `billable_default`, so the form, an
  import and an MCP call all answer the same — and a project a subscription covers has that
  default cleared the moment it is linked, because the retainer already pays for the work and
  billing it again is double-billing the client. It stays a *default*: the project can be
  ticked back, and any single entry's flag is the logger's call.
- **The uninvoiced report (#277)**: `GET /invoicing/uninvoiced` is the same "to invoice"
  predicate **org-wide** — no company scope — bucketed server-side (`group=day | week |
  month | year | company | project | user`) so the per-group subtotals (hours + amount, the
  #226 rate chain with the invoicing default folded into the SQL) are exact whatever the
  entry cap (`limit`, default 500; `truncated` says when the detail was capped — never the
  totals). Date buckets follow the org's local calendar (§8), computed in Postgres via
  `AT TIME ZONE`. Read-only and gated on `invoicing.invoice.read`: browsing the backlog is
  a view, building the invoice stays `from-time` behind `.write`.
- **Subscriptions (#30)**: the cycle cron emits `subscription.due`; this module's consumer
  (`events.py`) drafts one invoice per `(subscription, period)` — a claim lookup plus a partial
  unique index make a re-run, resume or double emit unable to double-bill. How far past the
  draft it goes is the tenant's `AutoInvoiceMode`. The org's default tax rate applies; the
  period rides `period_start`/`period_end`.
- **Domains (#250)**: the renewal cron emits `domain.due` with the price resolved *at the
  due date* (`price_override`, else the TLD's `domain_tld_prices` row valid then); the
  same `events.py` drafts one invoice per `(domain, period)` under its own claim table and
  partial unique index, one line ("Domeinverlenging …" in the org locale). Same level applies.
- **…on the day the registration actually lapses, which only a register knows.** `next_invoice_date`
  began life purely derived — the first yearly anniversary of `start_date` still ahead — and that
  is the true expiry exactly when `start_date` is the true registration date. For a portfolio
  onboarded in one afternoon it is not: every domain is anchored to that afternoon, and every
  renewal then invoices on the wrong day, every year, with no amount of re-saving the record
  fixing it because nothing ever asked the registrar. So a connected register that has *answered*
  now supplies the default, through `app/core/registrar/expiry.py` — the `presence.py` seam
  applied to a date, so `domains` still names no registrar and two registers holding one name
  resolve in a fixed key order rather than by import order. Four rules carry over from #298 and
  are what make it safe to ship into an instance already invoicing domains:
  - **A credential is not an authority.** A register speaks only through a row a *sync* wrote, so
    a connected-but-never-read account contributes nothing and every existing date stands.
  - **Only forward.** An expiry in the past is a lapsed registration — a thing to look at, not a
    date to bill on. Taking it would hand the cron a due date it fires on immediately and draft a
    renewal for a registration that has run out.
  - **Observed is not decided** (CLAUDE.md §10). `Domain.register_expires_on` is a second, read-only
    field beside the date schakl bills on; the list has a column for it and the detail view says
    "de registrar zegt …" when the two differ. Drift is *reported*, and the edit form offers it in
    one click — a mirror that silently overwrites cannot express "somebody changed this at the
    registrar" at all.
  - **The one-off correction skips what was billed.** The migration moves existing rows onto the
    observed expiry, but never a domain with an `invoice_domain_periods` claim: moving a period
    boundary underneath a claim is how a period gets billed twice or skipped entirely.

  The date is now editable everywhere the record is — form, spreadsheet and bulk selection — and
  an explicit `null` means *work the default out again*, not *stop invoicing*: "never bill this
  domain" is `invoiceable`'s job and already has a field. The import is the one surface where
  blank means **leave alone**, because in a file a blank column is what an export somebody edited
  two cells of comes back as, and rescheduling a thousand renewals is not something a blank should
  be able to say.
- **…but only if the domain is invoiced at all (#298).** An agency's domain list mixes names it
  registered and renews for the client with names the client registered themselves and merely
  asked us to point somewhere, and only the second kind must never reach an invoice.
  `Domain.invoiceable` is three-state and is **never read on its own**: `TRUE`/`FALSE` are a
  decision somebody made, `NULL` means *follow the registrar register* — bill it when a register
  the agency has actually read holds it, and keep billing while no such register exists. The
  resolution is one SQL clause (`domains/invoiceable.py`) that the cron's `WHERE`, the list
  filter and the picker all take, so a screen and the cron cannot disagree about which domains
  bill. Two consequences are worth stating here because they are easy to get backwards:
  - **The cron skips but still advances the cycle**, the discipline `AutoInvoiceMode.OFF`
    already follows. Freezing the date would leave a silent debt — switching the flag back on a
    year later would fire one missed year per run until it caught up.
  - **The picker lists it anyway, labelled.** Automation declining to draft a renewal and a
    human being forbidden to bill one are different things, and "why is klant.nl not on the
    invoice" is exactly the question the picker exists to answer. Answering by omission is how
    the duplicate happens — the same rule `already_billed` follows below.
- **The recurring backlog (#302)**: `GET /invoicing/recurring-backlog` is the *org-wide* other
  half of "nog te factureren" — agreement periods and domain renewals that no document claims,
  bucketed `company | month | source` with exact subtotals and a capped item list. Until it
  existed the recurring side was reachable only per client from inside the editor's picker, so
  "what do we have to invoice this month" had no screen, and **arrears had none at all**: the
  cycle cron advances whether or not it drafted anything, so a period automation was off for
  sat there with nothing to surface it. Built on the *same* seams the picker uses
  (`open_agreements()` / `open_renewals()` now answer org-wide when given no `company_id`),
  deliberately — an org-wide answer assembled from a second rule would eventually disagree
  with the picker about what a client owes.
  Two exclusions and one flag, all deliberate. A **claimed** period is not outstanding, which
  is the exact opposite of the picker's rule and right for the same reason the picker's is: a
  picker is preventing a duplicate on a document you are building, a backlog is listing work,
  and work that is on an invoice is done. A **non-invoiceable** domain (#298) is never going to
  be billed, and a permanent row nobody can clear is how a backlog page stops being read. A
  **future** period is kept and flagged: billing a retainer in advance is ordinary, so it is
  the reader's call. Each row carries the `AutoInvoiceMode` its own agreement resolves to —
  which is what the cron will do at that agreement's **next** boundary, never a promise about
  the row it sits on, because every period listed here has already been passed by the cycle.
  `resolve_auto_invoice_mode` lives in `app/core/billing.py` so the report and the `*.due`
  consumers cannot drift about what "follow the organisation" means.
- **What is still outstanding** (`GET /invoicing/outstanding`): the four buckets the editor's
  sections pick from, in one round trip. Each module answers the half it owns through its
  published interface (§6) — `SubscriptionService.open_agreements`,
  `DomainService.open_renewals` — and this module adds the half it owns: whether a period is
  already claimed.
  **Periods are walked backwards from the cycle's own `next_invoice_date`** in interval steps
  (`app/core/billing.period_boundaries`), because that is the grid the cron bills on: it
  advances by exactly one period per fire whether or not it drafted anything, so stepping back
  lands on precisely the boundaries it passed — including the ones automation was off for.
  Deriving the grid from `start_date` is the tempting mistake: `next_invoice_date` is
  operator-settable and routinely does not sit a whole number of periods from the start.
  Three bounds keep it honest: a period beginning before the agreement did was never served;
  the record's own `created_at` is the floor, which is #250's *onboarding an old domain never
  back-bills history* stated as arithmetic; and a cap of 24, **reported** rather than silently
  cut. Claimed periods are returned marked `already_billed`, never omitted — "did I invoice
  March?" is the question the picker exists to answer, and answering by omission produces the
  duplicate.
- **Quotes → invoices**: `convert` (accepted only) copies the lines *with their snapshots* —
  the deal keeps the prices it was accepted at. The quote flips to `invoiced` and points at
  the invoice; deleting that draft reverts it to `accepted`.
- **Companies (#11)**: `vat_number`, `coc_number` and the address live on `companies`
  (module-owned columns, in the form/impex/audit trail); documents snapshot them at issue.

## Automatic payment reminders (`jobs.py`)

Opt-in (`reminders_enabled`, default off) and bounded: `reminder_days` (e.g. `[7, 14, 30]`)
is the tenant's schedule of days past due; the daily per-org cron (`run_per_org`, org-local
calendar) sends at most one mail per step, to the document's snapshot e-mail (fallback: the
company's `invoice_email`), through the org's transport (#17). The counter only advances on
success — a transient SMTP failure retries the next day; a structural failure (no recipient,
no transport) is recorded **once** on the invoice's activity trail (`reminder_failed`),
never as daily noise. `reminders_paused` mutes one invoice; a manual
`POST /invoices/{id}/remind` sends the same mail and counts the same way. The same cron
expires open quotes past `valid_until`.

Every send, reminder, payment, issue, cancel and credit lands in the activity trail (§16),
so a disputed invoice's history reads back in one place.

## Online payments (epic #269 — `payments.py`, `docs/PAYMENTS.md`)

A client can pay an open invoice on a payment provider's hosted checkout, and the payment lands
back on the invoice by itself. The architecture is provider-independent and lives in
**`docs/PAYMENTS.md`**; `docs/MOLLIE.md` is the first (and today only) implementation. What
belongs *here* is the part invoicing owns:

- **A confirmed payment writes an ordinary `InvoicePayment` row** — `method="online"`,
  `intent_id` set — through the same `_settle` a bookkeeper's manual entry goes through. So
  `paid_total`, the status flip, `invoice.paid`, the reminders cron, the accounting export and
  the company panel all needed no change and none of them knows a provider was involved.
  `PaymentWrite.method` stays a closed `Literal` **without** `online`: nobody may hand-register
  a payment as though a provider had confirmed it.
- **`invoice_payment_intents` is one row per *attempt*, not per invoice.** An invoice
  legitimately collects several (iDEAL expires in fifteen minutes; clients abandon and retry),
  which is exactly why it is not an `ExternalRef` — that table's one-row-per-local-record key
  would let a late callback for attempt #1 settle against attempt #2.
- **The amount is never the caller's.** `InvoicePaymentIntentCreate` carries no amount field;
  the server charges `outstanding_of(invoice)`, recomputed at creation, and refuses an invoice
  that is not `open` or has nothing left to pay.
- **`status` is the provider's word and `settled_at` is ours.** `paid` with no `settled_at` —
  the money arrived and the ledger write did not happen — is a real, visible, repairable state,
  and an hourly per-org cron plus a manual **Check status** button exist to repair it.
- **Two permissions, and a client holds one of them.** `invoicing.payment.link` is scoped
  (`:own` for the `client` role, `:any` for admins) and is *not* `invoicing.payment.write`:
  starting a checkout settles nothing, while registering a payment is a bookkeeping claim.
  `InvoiceRead.online_payment` is the boolean the portal draws its pay button from — it never
  gets to read which accounts the agency has connected.
- **Every invitation to pay leads to a page of ours, never to a checkout URL** — the invoice
  mail's button and the reminder's, the document's QR (#268) and its pay-online line, and the
  portal's own button. `paylinks.py` is the one function that says so, and four surfaces
  pointing at one door is what makes "one open checkout per invoice" true: a checkout URL is a
  bearer credential that spends money, it expires in minutes while the invoice does not, and a
  client holding both a mailed link and a portal button can pay the same debt twice. The mail's
  button is the strict one — it appears only when a provider is connected and the invoice is
  collectable, so an instance without payments sends the mail it always sent
  (`docs/PAYMENTS.md` §9). *Which* page of ours is §"De publieke factuurlink" below.
- **A payer who comes back sees their own money** (#304). Mollie's callback is asynchronous and
  makes no ordering promise against the browser redirect, so the return landed on a page whose
  read had already happened: the invoice said *open* to the person who had just paid it, and
  the only control that could fix it was **Check status**, which is `:any` and therefore not
  theirs. The return URL now carries `?return=1`, the landing asks once server-side, and the
  page polls a few times while an attempt is still in flight. Bounded at the API
  (`refresh_pending`): non-final attempts only, and one provider call per attempt per five
  seconds, counted on its own `refreshed_at` — **not** on `synced_at`, which the *create* also
  writes and which therefore made the one case this exists for the one case it skipped.

## De publieke factuurlink (#304 — `public.py`)

An issued invoice has its own web address, and anybody holding it can read that invoice and pay
it without an account: `https://<tenant host>/invoice/<token>`.

**Why it exists.** #268 sent the QR to the client portal and argued that this was safe because
"the right person lands on the invoice, anyone else lands on a sign-in screen". True, and
answering the wrong question: the portal is a licensed product an agency buys per client
(`docs/PORTAL.md`), so most clients have no login — and for them the sentence read *everyone
lands on a sign-in screen*. A QR whose only outcome is a login form for an account you do not
have is #253's control that always refuses, printed on paper and posted.

**What the token is.** `invoices.public_token`, `secrets.token_urlsafe(32)` — 256 bits — minted
at issue, and lazily on first render for the register that predates the feature. Deliberately
not a UUID: 122 bits would also be unguessable, but a UUID in a URL *reads* as an identifier,
gets pasted into tickets and spreadsheets as though it were one, and carries no hint that it is
a credential. `NULL` means no public link, and the read refuses a NULL rather than treating it
as a wildcard.

**What it grants, exactly.** Read this one invoice, download its PDF, open a checkout for what
it still owes, and ask whether that checkout has landed. That is the same thing handing somebody
the paper invoice already grants — look at it, and pay what it says — and paying someone else's
bill is not an attack. It grants **no** second document, no company record, no contact, no
activity trail, and no other write.

**How that is enforced, and why it is not a list of things to remember.** The routes build a
`RequestContext` that is a *client-portal session scoped to one company* — `is_portal=True`,
`company_scope={invoice.company_id}`, and two permissions at `:own`
(`invoicing.invoice.read:own`, `invoicing.payment.link:own`). Every narrowing then comes from
machinery that already exists and is already tested: the company horizon fences the rows,
`Invoice.__portal_horizon_clause__` hides drafts, `_PortalDocumentRepository` applies both on
every path (§#266, §#285), and the module's own `:any` surfaces — the seller's bank details,
the price list, the template library, the unbilled backlog with every employee's rate on it —
are refused by the same dependency that refuses them to a client. The obvious shortcut,
`jobs.system_context`, holds `*` and would have made all of that a matter of this one file
remembering.

**The other four properties**, each of which has a plausible wrong version:

- **The lookup is tenant-scoped.** `org_id = :oid AND public_token = :token`, with RLS bound
  first, exactly like the payment callback's five gates. Looking a token up across tenants would
  be a second unscoped crossing and would answer before authenticating.
- **The token never travels in a `Referer`.** It is a path segment and the very next thing a
  payer does is leave for a payment provider, so every public response — page, document, PDF —
  sets `Referrer-Policy: no-referrer`. The app default (`strict-origin-when-cross-origin`)
  strips the path cross-origin but still sends the whole URL same-origin, which is not enough.
  `X-Robots-Tag: noindex, nofollow, noarchive` rides along, because a link mailed to a client
  ends up in signatures, tickets and helpdesk threads.
- **Every refusal is the same bare 404.** Unknown token, a draft's token, a suspended org, a
  tenant with the switch off. Distinguishing them tells an enumerator which guess was closer and
  helps nobody holding a real link.
- **The switch is retroactive.** `invoicing_settings.public_invoice_links` (Instellingen →
  Facturatie, on by default) is checked *before* the token is compared, so unticking it
  withdraws links that are already on paper — the only useful meaning of an off switch for a
  credential the agency cannot collect back.

**What is deliberately *not* mitigated, and why.** There is **no rate limit on token guessing**,
because at 256 bits guessing is not a threat model — an attacker managing ten thousand attempts
a second for a century covers about 2⁻¹⁹⁷ of the space, and a limiter there would only add a
knob that reads as protection. The one bound worth being aware of is different: `/pdf` renders
through WeasyPrint on every call, so a holder of a *valid* token can spend CPU by looping it.
That is a resource question rather than a disclosure one (they may read that document), the page
itself frames the cheap HTML and only an explicit download reaches the PDF, and it is called out
here so that the day it matters, the fix is a render cache keyed on
`(invoice, template, updated_at)` rather than a scramble.

**Printing the address is its own switch.** The pay-online block (`payment_link`) has two
fields, `label` and `url`, both on by default and toggled in the template editor's Layout tab
like any other. Before #304 they were one thing, and that was fine while the address was
`/invoices/<uuid>` — long, inert, meaningless to a reader. It stopped being fine when the
address became a **capability token in plain type**: printed, it is readable over a shoulder, in
a photocopy left on a shared tray, and in any screenshot of the invoice. The QR carries none of
that, because a code is not human-readable at a glance. So an agency that wants the convenience
without the naked credential switches `url` off and keeps a line the PDF everybody actually
receives still follows. With both off the block prints nothing and the QR's caption comes back,
because there is no longer a line for it to stand down beside.

Staff see the link on the invoice screen (`InvoiceRead.public_url`, detail read only) so they
can hand it over when a client rings up. It is empty for an external login: a client is already
looking at the document, and a bearer token on their own screen is a thing to forward by
accident. The surface is excluded from the MCP tool map for the same reason `/auth` is — a route
that authenticates *itself* does not travel `require_context`, which is the proxy's whole safety
argument.

## The client-facing mails are the tenant's to write (`emails.py`)

The invoice, quote and reminder mails are **customisable kinds** (`invoicing.invoice`,
`invoicing.quote`, `invoicing.reminder`), contributed on the module descriptor and edited per
locale in Instellingen → E-mail like the auth mails already were (#161 tier 2, `docs/EMAIL.md`).
They are the mails an agency's *clients* read, and until now the one piece of outgoing text
nobody could reword — an agency that had spent a week on its invoice design was still dunning
in ours.

- **A missing override changes nothing.** No schema, no migration, no behaviour: the built-in
  catalog text is the fallback, so an instance that upgrades sends exactly what it sent before.
- **Both paths honour it**, request (`/send`, `/remind`) and cron (`jobs.py`) alike. Customising
  only the manual send would customise the exception: every dunning mail an agency actually
  sends comes off the schedule, and the auto-send pass mails the invoices too.
- **The document's locale decides**, for the words *and* the template lookup, so a German
  invoice reads a German override or a German default and never a mix.
- **The plaintext part is always the catalog summary.** A tenant's HTML may say whatever it
  likes; the client still receives the number, the amount and the due date.
- The variables are per kind (`{number}`, `{company}`, `{contact}`, `{total}`, `{date}`,
  `{due_date}`, `{reference}`, plus `{valid_until}` on a quote and `{outstanding}` / `{days}`
  on a reminder) and the editor's test send previews them against the **same fabricated
  document** the PDF template editor draws — same currency, numbers that add up.

## Accounting (#31's seam, shipped ahead of the first live provider)

- **UBL 2.1 export** (`ubl.py`, `GET /invoices/{id}/ubl`): standards-shaped XML (EN 16931
  binding) that Exact Online, SnelStart, Moneybird and e-Boekhouden import today. Line
  amounts are net; on inclusive documents the per-group rounding drift folds into the
  group's largest line so `Σ line nets == TaxExclusiveAmount` to the cent. Category codes:
  positive rates `S`, zero `Z`, exempt `E`, reverse charge `AE` (with exemption reason).
- **Provider interface** (`accounting.py`): a live adapter implements `AccountingProvider`
  (`export_invoice(ctx, invoice, seller) -> ExportResult`) and self-registers; the router
  only talks to the registry (`GET /invoicing/providers`, `POST /invoices/{id}/export`).
  Adapters must treat a timeout as *unknown* — look the remote document up before retrying
  a create — and never receive the caller's credential (§12).
- **`invoicing_external_refs`**: what a package knows about a local record, unique per
  `(provider, local_type, local_id)` — the structural idempotency that makes "never create
  the same invoice twice" a constraint instead of a hope. `GET /invoices/{id}/refs` shows it.

## Bringing the back catalogue in (`impex.py`, `service.import_document`)

An agency moving onto schakl brings years of invoices from the package it used before —
Moneybird, SnelStart, e-Boekhouden, a spreadsheet — and without them the client hub has no
history, the outstanding tile starts on migration day and the PDF the client actually received
lives in a folder nobody links. They come in as a **spreadsheet through the impex engine**
(§17: the same wizard, preview and permissions as every other list — `ImpexBar` on Facturen,
`impex.import` + `invoicing.invoice.write`), one row per invoice **with its totals**, upserting
on `number`. Five rules hold it up, and the first two are the ones that generalise.

- **An imported invoice is an ordinary `Invoice` with provenance, never a second table.**
  `invoices.origin` is `native` or `imported`; an imported one is created *issued* (it has a
  number, so it is never a draft), with the sheet's status, and everything downstream — the
  company horizon, the portal clause, post-issue immutability, `OUTSTANDING_SQL`, the summary
  tiles, the reminders cron, the company panel — works unchanged because nothing about it is
  special except where it came from.
- **For a document somebody else issued, the totals are the fact, not the lines.** "Clients
  send lines, never totals" is the rule for documents *this platform* prices. A sheet states
  `subtotal`/`tax_total`/`total` (any one of the first two may be left for the difference, and
  all three present must agree to the cent — `invoicing.import.totals_mismatch`), and the
  service stores them **verbatim**. The document gets one summary line (`description`, or
  "Factuur {number}" in the document's locale; amount = subtotal at the *effective* rate, named
  after the tenant's own rate when one matches exactly) so the page has a row to print — but
  recomputing tax from that line at a two-decimal rate can be a cent off on a large mixed-rate
  invoice, so every breakdown a reader prints comes from **`calc.stated_totals`** through one
  `InvoiceService.document_totals`: the renderer, UBL and `InvoiceRead.tax_groups` cannot be a
  cent apart from each other or from the stored total. A credit note is stored negative
  whichever sign the sheet wrote it in, and `credit_for` (the source's number) rides the same
  `_apply_credit` a native note does at issue — so "credited" derives exactly as before. A
  reference is resolved against what exists *before* the preview, so a note travels in a
  later file than the invoice it corrects.
- **The payment columns describe a state, and the import records what makes it true.**
  `paid_total` is how much has been received and `paid_on` when; `status` alone means the whole
  document (`paid`) or nothing (`open`); an amount alone decides the status; both present must
  agree (`status_mismatch` — a sheet saying "paid" over "40 of 100" is telling two stories and
  picking one silently is what §17 refuses). A payment is a dated fact, so an amount without a
  date is refused (`paid_on_required`) rather than stamped with a day nobody stated. What lands
  is an ordinary `InvoicePayment` — which is why nothing else needs to know. The same columns on
  an **existing** row — native or imported — may only *raise* the state: the difference between
  the sheet's `paid_total` and what is registered is recorded as a payment, a lower figure is
  refused (`payments_reduced`; taking a registered payment back is a hand act with a trail), and
  any money column that *differs* is `invoicing.import.locked` while an equal one is not — so an
  export round-trips untouched and "mark these forty paid from the bank statement" is one file.
  Every one of these rules runs in the **preview**, naming the row and the column (#289):
  `ImpexDescriptor.validate_row` exists for this and calls the same two functions the write
  does (`plan_import`, `plan_import_update`), so the dry run can never pass a row the commit
  then refuses whole.
- **A bulk write of history emits nothing, and dunning is opt-in.** The import fires neither
  `invoice.issued` nor `invoice.paid` (`_settle(notify=False)`): an invoice paid three years
  ago is being *recorded*, not paid, and eight hundred rows must not become eight hundred
  notifications and automation runs. `reminders_paused` is **true** unless the sheet's
  `reminders` column says otherwise, because auto-mail from a new system about old invoices is
  a decision, not a default. SnelStart's automatic push sweep skips `origin = imported` (those
  were booked when they were issued); naming one explicitly still pushes it and a duplicate
  answer adopts. Instants the sheet cannot state (`paid_at`, `cancelled_at`, `sent_at`) are
  stamped with the sheet's own day at noon in the org's zone, never with "now".
- **The original document is a file the invoice names, and the record carries its own
  fingerprint.** An imported invoice may hold the PDF the client actually received
  (`original_file_id` → `files`, `ON DELETE SET NULL`), stored untouched — a document is
  evidence, the screenshot rule applied to paper — and `original_sha256` is written on the
  invoice *itself* when it is attached, so "is this still what was attached" is answerable from
  the record without trusting the blob table. Every reader of "the PDF" serves it in place of a
  render: `document_pdf` (the download, the mail attachment, the public link, the portal), while
  the HTML preview keeps rendering and the web decides what to frame. Only an `imported` invoice
  may carry one (409 `errors.invoicing.not_imported`): a native invoice's document *is* its
  render, and two documents under one number is the confusion this exists to avoid. PDF by
  declared type **and** by bytes. Attaching is `POST /invoices/{id}/original` (multipart, off
  the MCP surface by method) or its JSON twin — `POST /files/inline` against the invoice plus
  `PATCH {original_file_id}`; `null` detaches (§18); replacing goes through `drop_file`, never a
  bare storage delete. A migration has hundreds of these, so `POST /invoices/originals` takes a
  **zip** and matches each PDF to an imported invoice by its *file name* — exactly the number
  or a name containing it, separators and case ignored — and reports what matched, matched two
  numbers, matched none, was not a PDF, or was left alone because the invoice already held one;
  a batch never replaces a document somebody attached on purpose. The file reads for anyone
  **exactly when the invoice does**: `RECORD_GATED_ENTITY_TYPES` in `core/storage/service.py`
  routes `/files/{id}` through `entity_visible`, so a portal login of another client and a
  member outside the client's group get the same 404 the invoice route gives.

Two practical notes. The **numbering sequence** is untouched by an import — set
`invoice_next_seq` past the highest imported number in Instellingen → Facturatie, or the
allocator walks past collisions one at a time. And **crediting an imported invoice** works as
for any other (the note mirrors the summary line at the effective rate), but the note is a
native document whose totals *are* recomputed from that line, so on a large mixed-rate source
its draft may be a cent off the original — it is a draft, and you edit it before issuing.

## Multi-currency & locale

The org currency (#124) is the default; a document may carry any ISO 4217 currency with an
explicit `exchange_rate` (org currency per document-currency unit). The document itself is
entirely in its own currency; only the summary tiles convert (rate, else 1) for steering.
Each document carries a `locale`: the rendered page, the e-mails and the tax-name snapshots
all speak it, and templates carry per-locale intro/payment/footer texts — a Dutch agency
invoices a German client in the client's language without leaving its own.

## Web

Nav **Facturatie** is a sidebar submenu (#277, the Domeinen & websites pattern): **Facturen**
→ `/invoices` | `/quotes` (submenu tabs) and **Nog te factureren** → `/invoices/uninvoiced`.
The report page is read-only and covers **all three** sources (#302): four tiles across the
top — Uren, Abonnementen, Domeinen and the grand total, always counting everything — then a
source combobox choosing which one the table below details, a group-by combobox whose
vocabulary follows the source (Dag/Week/Maand/Jaar/Klant/Project/Medewerker for hours,
Klant/Maand/Soort for the recurring half), per-group subtotals rendered by `DataTable`'s
`groupSummary` snippet, expand/collapse per section, and every row — plus the group header
when grouped by Klant — linking to `/invoices/new?company=<id>` to actually build the invoice.
Two API calls, always both, because a tile that only appeared once you had clicked its tab
would not be a summary of anything (UX §7); the per-source narrowing is a filter over what
`source=all` already returned, never a second round trip. The two tables keep **separate**
column preferences — one shared id would let a renewal layout hide columns the hours table
has — and the save action reads which is on screen from the same `source` param the load did.
Lists are `DataTable`s with
summary tiles that filter the list they count (UX §7). The editor (`DocumentForm` +
`LinesEditor`) posts lines as one JSON field with one save button; issue/send/pay/credit are
explicit actions with confirms. UBL downloads proxy through
`/invoices/[id]/ubl` (the impex pattern: the browser can't reach the API host), and so does
the rendered document: `/invoices/[id]/preview` serves the API's HTML same-origin so
`DocumentFrame` can measure and print it. Instellingen → Facturatie holds seller identity, tax
rates, templates, numbering, defaults, reminders and the accounting section.

### The line editor is four sections

**Uren · Abonnementen · Domeinen · Diensten**, mirroring the bands the rendered document
already prints. It used to be one flat repeater with a kind `<select>` and a free `unit` box on
every row, and both asked a question with no interesting answer:

- A line's **kind** is not a per-row choice. It is which section you added it in — and the
  section is the thing that knows where to get real data.
- A line's **unit** is a property of its kind for three of the four. Hours are hours; a
  recurring fee is one period; a renewal is one year. Only a service line sells things measured
  in something (stuks, dagen, woorden), so that is the only section that still shows the field.
  The hours unit is resolved **API-side in the document's locale** (`invoicing.unit.hour`), so
  an invoice to a German client says "Std." whoever pressed the button — it used to be the
  Dutch literal `"uur"`, hardcoded, on every document in every language.

The first three are **picked, not typed**: each section's control opens `OutstandingPicker`
over `GET /invoicing/outstanding`, with a count badge on the button. Lines arrive priced, dated
and carrying what they bill, and that provenance round-trips. Each section's picker shows
**one** source (#302) — the dialog's title, lead and empty state all name it, and the offer-id
prefix is what carries the kind back, so a mixed dialog also made the resulting line's kind
depend on which row you happened to tick. `LINE_KINDS` in `types.ts` and `SECTION_ORDER` in
`render/context.py` must stay in step, or saving in the editor reorders the printed document.

Nothing is added behind your back. The old behaviour dropped **every** unbilled hour onto a
fresh invoice the moment you chose a client, which is a list to delete rather than a list to
choose from; the picker is not auto-opened either, because modal thrash on every company
change is worse than what it replaced.

The editor **always shows all three sections**, an empty one collapsing to its heading and its
add control — its job is to state where a line goes. The renderer keeps the opposite rule (a
document whose lines are all one kind gets no headers, because a lone "UREN" band subtotalling
to the subtotal beneath it is noise). The divergence is deliberate; do not "fix" either to
match the other. Section order **is** document order: the server takes `position` from the
posted array index, so the editor serialises section by section.

Instellingen → Facturatie carries **Automatisch factureren** (the org's `AutoInvoiceMode`), and
a subscription or domain overrides it in its own form with a "follow the organisation setting"
row that names the inherited level — omitted where the caller cannot know it, because a hint
naming the wrong level is worse than no hint.

The **document is shown in a frame, not redrawn** (`DocumentFrame.svelte`). It is the page the
API rendered and the PDF prints, scaled to fit rather than resized so the document's own CSS
keeps working in the pixels it was written for. The print pages ask the *frame* to print, so
the document's `@page` rules reach the printer instead of a screenshot of the app — which is
why they no longer carry a `@media print` block hiding the shell.

The **template editor** (`TemplateEditor.svelte`) is design / layout / texts / code beside a
live preview, and that preview is the API's real renderer working on the unsaved config —
debounced, because it renders a real document server-side. `templateConfig.ts`'s `mergeLayout`
mirrors `resolve_layout()` so the list and the paper agree about order; a drift shows up as
the two disagreeing on screen, which is the failure mode we want. The code tab only appears
for a caller holding `invoicing.template.author`, and that is UX — the API is the boundary.

## The document: one artefact (`render/`)

There used to be two renderers — `DocumentView.svelte` drew the invoice in Svelte, `pdf.py`
drew it again in fpdf2, and each carried a comment telling you to keep it in step with the
other. There is now **one**: `render/` builds a context, a Jinja design turns it into a
standalone HTML page, and WeasyPrint prints *that*. The preview endpoint serves the same
page and the web frames it. "The preview and the PDF disagree" is no longer expressible.

```
service._render_inputs ─┬─▶ context.build_context ──▶ engine.render_html ──▶ HTML
   (brand, seller,      │        (one dict:                (Jinja)            │
    template config,    │      strings, no ORM)                               ├─▶ /preview
    tax groups)         │                                                     └─▶ engine.html_to_pdf
                        └─ the layout, resolved against blocks.BLOCK_CATALOG        (WeasyPrint) ──▶ /pdf
```

- **The context is strings, never rows.** A tenant's own template renders against that exact
  dict in a Jinja sandbox; if it held ORM objects, "print the customer's name" and "walk the
  session to another org's invoices" would be the same expression.
- **Formatting is a property of the document, not the viewer.** Money, dates and labels
  resolve in the *document's* `locale` — a Dutch invoice to a German client prints
  `€ 1.234,56` and `30-06-2026` whoever opens it. Same rule the document e-mails follow.
- **Branding is runtime, per-tenant** (Golden Rule 4). The logo comes from its own stored
  bytes (`app/core/branding.py` reads the file id out of `org_settings.logo_url` and pulls it
  from the storage backend — never an outbound fetch of an org-controlled URL), and the
  accent falls back to `primary_color`. Nothing below `service` owns a default hex.
- **The accent is contrast-corrected against paper.** `document_accent()` darkens the tenant
  colour in HSL, hue preserved, until it clears 4.5:1 on white — `deriveOnDark` from
  `lib/core/theme.ts` pointed the other way, because the accent carries small text and a
  pale-yellow brand would otherwise print an invisible heading. `documentAccent()` in
  `types.ts` still mirrors it for swatches in the editor.
- **It reads like the app.** Ink, muted text, rules and washes are the light values of
  `app.css`'s tokens; the face is **Inter**, installed by `apps/api/Dockerfile`.
- **Page numbers arrive as a second stylesheet**, so a one-page invoice stays unnumbered.
  That costs a second layout pass, and only on documents that really run to two pages.
- **A field may carry a *note* beside its value** — "(voor 14-07-2026)" against the amount owed
  — which travels as its own piece rather than glued onto the value, so a design can set it
  apart. Glued on it was one bold string, and the deadline read as part of the sum.
- **The fallback "please transfer…" sentence stands down behind the payment card.** It exists
  so a document showing no card still says where the money goes; with the card on it is the
  same amount, IBAN and reference a second time a few centimetres lower. A sentence the tenant
  wrote themselves always prints — they put it there on purpose.
- **The VAT split is stated once.** `tax_rows` prints a line per rate — that split is what
  makes a multi-rate invoice lawful, which is why the field is locked. It is required *on the
  document*, though, not required *there*: with the `tax_summary` block switched on the same
  split is already stated beside it in more detail (taxable amount as well as tax), so the
  totals collapse to a single **Totaal btw**. Without that block the rows stay per rate,
  because then they are the statement. The reader's question at the foot of an invoice is how
  much VAT; only a document carrying several rates also has to answer which.

### A selection prints as one archive (#307)

`GET /invoicing/invoices/pdf?ids=…` is the bulk half of `/invoices/{id}/pdf`: the invoices
ticked in the list's ✎ mode come back as one zip, each entry filed under the number the single
download would have given it. Handing a month of invoices to an accountant was otherwise a
click per document. Four decisions hold it up, and none of them is about zip files.

- **It is a `GET`, twice over on purpose.** It is a read, and `license_write_gate` keys off the
  method — past a licence's expiry a module goes read-only, not gone, so a `POST` here would
  have locked an agency out of its own paperwork at the moment it wants to file it. It is also
  what lets the bulk bar's control be a real `<a href>` rather than a click handler that sets
  `location` (docs/UX.md).
- **The selection rides the scoped repository** (`InvoiceService.by_ids`, one query for the
  batch). An id this caller may not read is therefore **absent** from the archive rather than a
  403 that would confirm whose invoice it is, a client's login gets its own issued documents and
  no drafts through the very same clause the list obeys (#266, #285), and a selection that
  resolves to nothing is a 404 — an empty zip is not an answer.
- **Nothing in it is per row.** The seller block, the brand and the logo bytes are one read for
  the whole batch (`_render_shared`), the design is memoised per template id, and the lines are
  `_attach`'s grouped read — so a five-invoice archive issues exactly the statements a
  one-invoice archive does, which is what `test_invoicing_archive.py` pins. Resolving the org
  half inside `_render_inputs` is right for one document and is three round trips per document
  for fifty.
- **It caps at `MAX_ARCHIVE_DOCUMENTS` (50), declared on the route.** Every entry is a full
  WeasyPrint layout, so two hundred is a request no proxy will wait out and one with no progress
  to show for it; fifty is the pager's default page size, so "tick the page, download it" fits
  exactly. `MAX_IMPORT_ROWS`' reasoning: a cap is what keeps a synchronous batch honest until it
  is a background job. The web mirrors the number to *say* so (`invoicing/types.ts`) rather than
  letting the user press a control that will 422.

### What a template may rearrange (`render/blocks.py`)

A template carries a **layout**: an ordered list of blocks, each toggleable, each with its own
ordered list of toggleable fields. `BLOCK_CATALOG` is the registry those keys are drawn from —
§15's "registry, not free text" applied to design.

- **A stored layout is a diff, not a snapshot.** Resolution starts from the catalog and lets
  the layout reorder and toggle what it *mentions*; a block or field it has never heard of
  lands at its catalog position with its catalog default. Without that, every field added by a
  later release would be invisible to every existing tenant, and the first person to notice
  would be a customer reading an invoice missing its VAT number.
- **Regions belong to the design.** Only the body is genuinely a stack, so only the body
  reorders. A design may place a block by hand (the letterhead's payment card sits beside the
  addressee) — and when it does, it must still consult `enabled`, which `_entries()` now does
  centrally. It did not once, and the switch silently did nothing.
- **Legality is not a preference.** Locked blocks and fields — the number, the date, the VAT
  breakdown, the reverse-charge notice — may be moved but never switched off.
- **A field's label is the catalog's until the template rewords it.** "Telefoon" and "t" are
  the same field, and which one an agency prints is their letterhead, not ours — so a layout
  field carries an optional `label_i18n`, per locale like everything else a tenant writes. The
  catalog still owns the **key**, so an override is a display string and can never widen what a
  template names. Two rules keep it honest: a field that prints **no** label (`labelled=False` —
  the address lines) drops the override rather than gaining a label, because in the letterhead
  that would move the street out of the address stack and into the labelled grid; and a
  reworded label beats a **design's own shorthand**, so the letterhead's `t` / `e` / `i` answer
  our wording and never the tenant's, or the box they typed in did nothing.
- `show_logo` and `columns` predate layouts. They stay the input while a template has no
  layout of its own (so a release cannot redesign a document a tenant already approved), and
  the service rewrites them *from* the layout on save so the two can never disagree.

### The shipped designs

`classic` is what the product has always printed. `letterhead` is the shape a Dutch agency
invoice usually takes: sender across half the header, a boxed *Betaalgegevens* card by the
addressee, the VAT breakdown beside the totals, and the tenant's mark behind the page.

What makes the letterhead read as paper rather than as a screen, and why each piece is where
it is:

- **Two columns that flow independently, twice.** The wordmark, heading and addressee run down
  the left while the sender's identity runs down the right, so the heading sits *beside* the
  sender block instead of below it. Then the payment card and the document's numbers form a
  second pair, deliberately aligned at the top — letting those two simply follow their columns
  would align them by accident, and a sole trader with four sender fields would get a card
  floating a long way below its numbers.
- **The heading is black, in the case a letter is written in.** The catalog message is set in
  capitals for `classic`, which opens on a shouted `FACTUUR`; the letterhead corrects the case
  in CSS rather than in the catalog, because one design's typography is not the other's
  content.
- **The tenant's colour is spent once**, on the line-kind headings. Everything else — the
  heading, the amount owed — is ink, so a loud brand still prints as paperwork.
- **The paper is ruled in the tenant's colour.** `--accent-line` under the column headings and
  over the amount owed, `--accent-hairline` for the quieter separations, and the two washes
  (payment card, closing band) tinted to match. Both are *tints* of the accent, not the accent:
  a solid brand colour under every heading competes with the words above it, and
  `document_accent` darkened the hue to carry text, not lines. It is also what lets the type
  stay black — the colour is in the ruling, so the heading and the total need not shout it.
- **A line kind gets its own headed table, not a band across a shared grid** (`sectioned` in
  `_blocks.html`). One grid banded three times made *Aantal* mean hours at the top and licences
  in the middle, with the heading that said so eighteen rows up. The kind names the description
  column — it is the heading of exactly that — so it costs no row of its own, and living in the
  `thead` it reprints when a long group runs over the page. One kind falls through to the plain
  ruled table: a lone "UREN" over a table that subtotals to the subtotal beneath it is noise,
  the same rule `_sections` already applies to the grouping itself.
- **One washed band closes the document**, VAT breakdown left and totals right, settled onto
  the same baseline rather than the same top — the last row of the breakdown reads across to
  the amount due, which is the comparison the band is for. It is a **table and not flex**:
  WeasyPrint fragments a flex container by leaving its children behind, so an invoice that
  ended near the foot of a page printed the band as an empty grey rectangle with its numbers
  alone overleaf.
- **The band is drawn by hand, and the body loop splits around it.** `tax_summary` and `totals`
  leave the stack the way `payment_box` already does, and the band lands where `totals` was
  ordered — so "notes below the total" still means what it says. `.flow` carries a *floor*
  rather than the band carrying a ceiling: a short invoice keeps the open middle the design is
  built around, and a long one grows past it. Pinning the band to the foot of the sheet would
  need the page's height, which no block in normal flow can ask for without risking a break of
  its own.
- **How to pay is one box.** The QR (#268) and the pay-online line (#269) are body blocks in
  `classic`'s stack; here they are drawn by hand *inside* the payment card, under a rule —
  bank transfer above, one gesture below. Left in the loop they landed centimetres lower, in
  the open middle of the sheet, with the reader's eye crossing the line table to get from the
  IBAN to the code that is an alternative to it. The card takes them through `{% call %}`
  (`payment_card` renders `caller()` if it has one), so `classic` is untouched and a tenant
  branching from either still gets the plain card. With `payment_box` switched off the strip
  still prints, on its own: the left column is where this design puts how to settle the
  invoice, box or no box, and hanging the pair off the card would have made `payment_box` a
  silent third switch on both of them.
- **The QR's caption stands down beside the pay-online line.** "Scan om te betalen" draws the
  same distinction the line's own label draws — betalen against bekijken — about a picture
  nobody needs told is scannable, and under the address it reads as belonging to the address.
  With the line off it is the only thing saying the code is worth pointing a phone at, and it
  prints. A tenant who writes their own caption (`qr_caption_i18n`) always gets theirs: they put
  it there on purpose, and they know whether they have a provider connected.
- **The QR is fully configurable, and the rule that made it safe grew rather than went**
  (#305, on top of #269). `qr_style` is now `brand` (accent + logo, the default and unchanged) ·
  `plain` (mono) · `custom`, and `custom` unlocks `qr_color`, `qr_background`, `qr_logo`
  (`brand` / `none` / an uploaded `qr_logo_file_id`) and the caption. #269 shipped no picker on
  the argument that "letting a tenant type a hex here would be offering them a way to print an
  invoice nobody's phone can read" — right about the danger, wrong about the remedy. What kept
  a pale accent scannable was never the absence of a field, it was `readable_dark`; so that
  became **`readable_pair`**, which judges both colours together, and the editor gained a live
  preview of the actual code plus a sentence explaining any substitution. Three refusals, and
  each falls back to black-on-white **as a pair**: too little contrast between them (nudging
  only the ink leaves a mid-grey panel that passes a ratio and still loses a camera), a "light"
  side that is not light (`MIN_LIGHT_LUMINANCE` — an inverted code clears every contrast check
  and scans worse everywhere, so "no dark mode" is now a number instead of a missing option),
  and a "dark" side that is the lighter of the two. `qr_appearance` (`render/context.py`) is the
  single resolution, read by the document, the mail's PNG *and* the editor's preview — which
  also fixed a quieter #269 bug: the mail drew the org's brand colour unconditionally, so a
  template set to `plain` printed mono on paper and mailed a coloured code. One more mechanism
  worth knowing: segno's SVG writer omits light modules entirely (that is why the file is
  small), so a tinted background there is a full-bleed `<rect>` of ours behind the symbol, while
  the PNG can just be handed a `light` colour.
- **A rule that styles an inline element does nothing, and the QR is the scar.**
  `.payment-qr-code { width: 24mm; height: 24mm }` sat on an `<a>`, which is inline: width and
  height do not apply, the svg's `100%` resolved against the paragraph instead, and the code
  printed the full width of the sheet — in both designs, in the preview and in the PDF alike,
  running the sample to three pages. Every test read the markup, and the markup was right. So
  the anchor is `inline-block` now and the guard measures the **laid-out box** through
  WeasyPrint (`_boxes` in `tests/test_invoicing_render.py`): anything about size or arrangement
  has to ask the layout, because the HTML is not the document.
- **The printed URL is never set in `micro`.** That class uppercases, and a URL path is
  case-sensitive: what reached the paper was `HTTPS://…/INVOICES/6F1A…`, against a route that
  is `/invoices/[id]`. The one reader the printed address exists for is the one who cannot
  click it, and they were being handed a 404. The assertion is on the *rendered* text, since
  the markup carried the right characters throughout.
- **Density is part of the design.** The sample — three line kinds with subtotals, two VAT
  rates, a partial payment, a footer — prints on one sheet, and a test says so, because that
  sample *is* the editor's preview. It ran to two once, with the totals stranded alone on the
  second. That test is bound to the **default** layout on purpose: the card, the code and the
  line all ship off, and switching every optional block on at once still costs a second sheet
  — buying that back would mean making every ordinary invoice tighter than the paper it models.

The background is **opt-in**: a template stored before it existed has no `background` key, and
reading that as "yes please" would have put a mark behind every invoice every tenant had
already approved. Absent an image of its own it uses the org logo, so the design works the
moment it is picked. Every number in it is re-clamped at render time — the config is
tenant-writable and a stored opacity of `40` would black out the text.

### Bring your own design

`design: "custom"` renders the tenant's own Jinja against the same context, inside our shell
(A4 geometry, the palette, the draft watermark). Authoring is gated on
`invoicing.template.author` — arranging blocks is `settings.manage`, but writing code that
runs on the agency's server is a strictly larger act. An **unchanged** body passes the check,
so an admin without the permission can still rename a template that carries custom HTML.

Two walls, both tested in `tests/test_invoicing_render.py`:

- **A sandboxed environment with no loader.** `SandboxedEnvironment` refuses attribute
  traversal into Python internals, so `{{ ''.__class__.__mro__ }}` raises instead of
  resolving; `{% include %}`/`{% extends %}` have nothing to resolve against.
- **Nothing is fetched.** WeasyPrint's `url_fetcher` answers `data:` and raises on everything
  else — one rule covering `file:///etc/passwd`, `http://169.254.169.254/` and a slow CDN
  alike. Every image a document legitimately shows is inlined as a data URI upstream, so the
  shipped designs never need the network either. A refused image costs the image, not the
  invoice: WeasyPrint logs and skips it, and the document still prints.

The block macros reach templates through the *context* rather than `{% import %}`, which is
what makes a design's body file portable: the same markup runs as a shipped design and as the
starting point of a tenant's own. "Start from this design" hands over the very files the
shipped design renders from.

## Extending

- **A new tax seed set** is a `taxseeds.py` entry — data, not logic.
- **A live accounting provider** is a new module registering an `AccountingProvider`;
  credentials encrypted per tenant (the email-settings pattern), sync state in
  `external_refs`. **Shipped**: `snelstart` (#377) is the first live provider — see `docs/SNELSTART.md`. #31 holds the original scope.
- **A payment provider** is a new module implementing `app.core.payments.PaymentProvider` and
  registering an account resolver — nothing in `invoicing` changes, and nothing in it may name
  the provider. The seam, the five callback gates, the idempotency pair (a row lock plus the
  partial unique index on `invoice_payments (org_id, intent_id)`) and a step-by-step checklist
  for adding Stripe or Adyen are in **`docs/PAYMENTS.md`** §11; `docs/MOLLIE.md` is the worked
  example. One webhook route serves every provider
  (`POST /invoicing/payments/webhook/{provider}/{token}`) — do not add a second, and do not
  believe a callback body: the authenticated re-fetch is the authentication.
- **E-invoicing networks (Peppol)** are a follow-up; the seams (the rendered document, UBL,
  payments as first-class rows) are where they attach.
