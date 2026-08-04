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
`/invoices/[id]/ubl` (the impex pattern: the browser can't reach the API host), and so does
the rendered document: `/invoices/[id]/preview` serves the API's HTML same-origin so
`DocumentFrame` can measure and print it. Instellingen → Facturatie holds seller identity, tax
rates, templates, numbering, defaults, reminders and the accounting section.

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
- **The lines table is ruled, not filled.** Column headings in the words themselves with a rule
  above and below; no fill, no per-row borders. A section is set off by air and one hairline:
  on an invoice that mixes worked hours, agreements and sales the reader has to *find* the
  three groups, not be walled off from them.
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
- **Density is part of the design.** The sample — three line kinds with subtotals, two VAT
  rates, a partial payment, a footer — prints on one sheet, and a test says so, because that
  sample *is* the editor's preview. It ran to two once, with the totals stranded alone on the
  second.

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
  `external_refs`. See #31 for the SnelStart scope.
- **E-invoicing networks (Peppol), payment-provider webhooks** are follow-ups; the seams
  (the rendered document, UBL, payments as first-class rows) are where they attach.
