# Invoicing — invoices & quotes (issue #207)

> The native billing suite: invoices and quotes raised inside the CRM, wired into time
> tracking and subscriptions, with tenant-configurable tax, templates, numbering and
> automatic payment reminders. Read this before touching `apps/api/app/modules/invoicing/`
> or the web module.

## The shape

Two documents, one engine. `invoices` and `quotes` are separate tables and endpoints (their
statuses and numbering differ) sharing the calculation (`calc.py`), the tax snapshots, the
templates and the rendering (`DocumentView.svelte`). Everything is org-scoped + RLS-forced,
custom-fieldable (§13) and auditable (§16), like every module.

```
draft ──issue──▶ open ──payments cover total──▶ paid
  │                │◀───payment removed──────────┘
  │delete          │cancel (no payments)
  ▼                ▼
gone           cancelled

quote: draft ──issue──▶ open ──▶ accepted ──convert──▶ (invoice draft)
                          │  └─▶ rejected                    │
                          └────▶ expired (cron, past validity)
                     accepted ◀─── deleting that draft reverts the quote
```

**Overdue is derived, never stored**: `open` + `due_date` before the org-local today. The
list, the summary, the company panel and the reminders cron all compute it the same way.

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
- **Issued invoices don't delete — they cancel.** Delete is draft-only; cancel requires no
  registered payments and releases any billed time entries — and any claimed subscription
  periods, so cancelling never retires an agreement's month for good.
- **A line knows what it is** (`line_kind`: `hours` / `subscription` / `product`). An
  agency's invoice mixes worked hours, recurring agreements and one-off sales, and the
  reader has to tell them apart — "24 uur × € 95" and "Hosting maart" answer different
  questions. So the kind is stamped by *whoever builds the line* (`from_time` → hours, the
  cycle cron → subscription, a product pick or a hand-typed line → product) and travels to
  the document, which groups and subtotals by it. It is presentation and provenance, never
  money: totals are computed exactly as before. A document whose lines are all one kind
  gets **no** section headers — a lone "UREN" band subtotalling to the subtotal beneath it
  is noise; headers earn their place when two kinds must be told apart. A credit note and a
  quote conversion carry the source document's kinds over.
- **A billed subscription period is claimed, so the cron knows it is already paid**
  (`invoice_subscription_periods` — `invoice_time_entries` for agreements). One column on
  `invoices` holds one agreement and one period, while a hand-built invoice routinely
  carries three subscriptions plus some hours; so the claim moved to its own table, keyed
  `(org, subscription, period_end)`, and `on_subscription_due` consults it before drafting.
  A second document claiming the same period is refused with
  `errors.invoicing.period_already_billed` rather than left to 500 on the unique index. The
  claim is rebuilt from the lines on every write: drop the subscription line and the period
  goes back to the cron. The lookup on `invoices.subscription_id` stays as the backstop for
  rows the cron drafted before the table existed.

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
  exactly those and nothing else. `GET /invoicing/unbilled` feeds the dialog. An entry can
  be on one invoice, ever (unique constraint).
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
  (`events.py`) drafts one invoice per `(subscription, period)` — a lookup plus a partial
  unique index make a re-run, resume or double emit unable to double-bill. **Draft, never
  auto-issued**: a human sends invoices (#31's rule). The org's default tax rate applies;
  the period rides `period_start`/`period_end`.
- **Domains (#250)**: the renewal cron emits `domain.due` with the price resolved *at the
  due date* (`price_override`, else the TLD's `domain_tld_prices` row valid then); the
  same `events.py` drafts one invoice per `(domain, period)` under its own partial unique
  index (`uq_invoices_domain_period`), one line ("Domeinverlenging …" in the org locale),
  same draft-only rule.
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
The report page is read-only: a group-by combobox (Dag/Week/Maand/Jaar/Klant/Project/
Medewerker), per-group subtotals rendered by `DataTable`'s `groupSummary` snippet from the
API's own aggregate, expand/collapse per section, and every row — plus the group header when
grouped by Klant — links to `/invoices/new?company=<id>` to actually build the invoice.
Lists are `DataTable`s with
summary tiles that filter the list they count (UX §7). The editor (`DocumentForm` +
`LinesEditor`) posts lines as one JSON field with one save button; issue/send/pay/credit are
explicit actions with confirms. Lines are added three ways, one per kind: `＋ regel`,
`＋ urenregel` (unit and the org's default hourly rate prefilled), the product-preset picker,
and `＋ abonnement` — which lists the client's active agreements from
`GET /invoicing/billable-subscriptions` with the amount, the period and, for a period a
document already holds, `al gefactureerd`. Shown, never hidden: "did I invoice March yet?"
is answered on the picker instead of by a duplicate. UBL downloads proxy through
`/invoices/[id]/ubl` (the impex pattern: the browser can't reach the API host). Instellingen → Facturatie holds seller
identity, tax rates, templates (with live preview), numbering, defaults, reminders and the
accounting section.

## The document: one design, two renderers

`DocumentView.svelte` (preview + print page) and `pdf.py` (the download and the PDF the send
path attaches) are a **matched pair**: same blocks in the same order, same palette, same
grouping, same accent. Change one, change the other — a client who reads the preview and
then opens the PDF must not see two different documents. Three rules hold them together:

- **Branding is runtime, per-tenant** (Golden Rule 4). The PDF draws the tenant logo from
  its own stored bytes (`app/core/branding.py` reads the file id out of
  `org_settings.logo_url` and pulls it from the storage backend — never an outbound fetch of
  an org-controlled URL) and the accent falls back to the tenant's `primary_color`. A PDF
  with a hardcoded hex is a white-label product printing someone else's identity.
- **The accent is contrast-corrected against paper.** `document_accent()` / `documentAccent()`
  darken the tenant colour in HSL, hue preserved, until it clears 4.5:1 on white — the
  `deriveOnDark` trick from `lib/core/theme.ts` pointed the other way, because the accent
  carries small text (section labels, the total) and a pale-yellow brand would otherwise
  print an invisible heading. The two implementations must agree byte for byte.
- **It reads like the app.** Ink, muted text, rules and washes are the light values of
  `app.css`'s tokens; the PDF face is **Inter**, installed by `apps/api/Dockerfile`. Without
  a real font on disk fpdf2 falls back to the built-in Helvetica, which is latin-1 only —
  that is what printed every `€` as `?`.

The template resolves the same way in both: the document's own template, and nothing implied
when it has none. Row heights are measured from the wrapped description (`dry_run`) before
the row is drawn, so a two-line description can never be overprinted by the next row, and
the table header repeats after a page break.

## Extending

- **A new tax seed set** is a `taxseeds.py` entry — data, not logic.
- **A live accounting provider** is a new module registering an `AccountingProvider`;
  credentials encrypted per tenant (the email-settings pattern), sync state in
  `external_refs`. See #31 for the SnelStart scope.
- **E-invoicing networks (Peppol), payment-provider webhooks** are follow-ups; the seams
  (the rendered document, UBL, payments as first-class rows) are where they attach.
