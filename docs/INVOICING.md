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
  registered payments and releases any billed time entries.

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
- **Subscriptions (#30)**: the cycle cron emits `subscription.due`; this module's consumer
  (`events.py`) drafts one invoice per `(subscription, period)` — a lookup plus a partial
  unique index make a re-run, resume or double emit unable to double-bill. **Draft, never
  auto-issued**: a human sends invoices (#31's rule). The org's default tax rate applies;
  the period rides `period_start`/`period_end`.
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

Nav **Facturatie** → `/invoices` | `/quotes` (submenu tabs). Lists are `DataTable`s with
summary tiles that filter the list they count (UX §7). The editor (`DocumentForm` +
`LinesEditor`) posts lines as one JSON field with one save button; issue/send/pay/credit are
explicit actions with confirms. `/invoices/[id]/print` renders `DocumentView` with print CSS
hiding the shell — the browser's Save-as-PDF is the PDF path until a server-side renderer
ships (a stated follow-up). UBL downloads proxy through `/invoices/[id]/ubl` (the impex
pattern: the browser can't reach the API host). Instellingen → Facturatie holds seller
identity, tax rates, templates (with live preview), numbering, defaults, reminders and the
accounting section.

## Extending

- **A new tax seed set** is a `taxseeds.py` entry — data, not logic.
- **A live accounting provider** is a new module registering an `AccountingProvider`;
  credentials encrypted per tenant (the email-settings pattern), sync state in
  `external_refs`. See #31 for the SnelStart scope.
- **PDF files, e-invoicing networks (Peppol), payment-provider webhooks** are follow-ups;
  the seams (print view, UBL, payments as first-class rows) are where they attach.
