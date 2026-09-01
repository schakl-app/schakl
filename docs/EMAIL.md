# E-mail — architecture and HTML template rules

> Read this before composing a new outgoing mail or touching the templates/branding layer.
> Every mail the platform sends is **branded, multipart HTML** (#236): tenant logo, brand
> name and primary color from `org_settings`, a plaintext part that always works on its own.

## What sends mail, and from where

| Mail | Composed in | Sent through | Tenant-customisable |
|---|---|---|---|
| Password reset / invite | `app/core/auth/emails.py` + `core/email/templates.py` | `send_org_email` | yes (`reset`, `invite`) |
| Notification (immediate, digest, per-channel) | `app/modules/notifications/external.py` + `render.py` | `send_org_email` | no |
| Channel / e-mail settings / template test sends | `notifications/channel_admin.py`, `core/email/service.py` | `send_org_email` | n/a |
| Invoice / quote / reminder (request + cron) | `app/modules/invoicing/emails.py`, `jobs.py` | `send_email` directly¹ | yes (`invoicing.*`) |
| Custom-domain alert (cloud, daily sweep) | `app/core/cloud/domain_alert.py` | `send_org_email` | no |
| Task assigned to a client contact (#454, worker job) | `app/modules/tasks/emails.py` | `send_org_email` | yes (`tasks.assigned_contact`) |

¹ The invoicing request path does its network call inside `ctx.release_db()` and the worker
has no request, so both bypass `send_org_email` — they call `apply_branding` themselves.
If you add a third bypass, you own the same obligation. Both **compose before the split**: an
override is an ordinary org-scoped read, so it must be resolved while the session is still ours,
never inside `release_db`.

## Which transport sends, and how the org is told

`send_org_email` resolves in one order, and every surface that reports on e-mail must agree
with it:

1. the org's own `email_settings` row (SMTP / Brevo / SendGrid / SMTP2GO), else
2. the **operator's** transport (`SCHAKL_INSTANCE_EMAIL_*`) — either chosen explicitly
   (`provider="instance"`) or fallen back to with no row at all, and in both cases only
   while `orgs.email_included` is true for this org (epic #199, `instance_email_allowed`),
   else
3. nothing: `(False, "errors.email_not_configured")`.

**Stored ≠ active, and the API says both.** Included e-mail stores nothing, so a settings
read that only described storage claimed "not configured" while every mail was going out
fine — and offered a blank SMTP form under it. `GET /settings/email` therefore always
returns an object carrying `active_provider` / `active_from_email` / `active_from_name` (as
whom it actually leaves — on included e-mail, the instance's address under the org's own
brand) plus `instance_email_available` for *this* org. `provider = null` means nothing is
stored; `active_provider = null` means nothing can send. A new surface answers "is e-mail on
here?" from `active_provider`, never from the presence of a row.

The entitlement is operator state on `orgs`, written only from the instance/provisioning
surface (Console → org, or `email_included` on org creation, default true). The tenant
chooses whether to *use* the included transport, never whether they have it — and a
withdrawn entitlement stops a stored `provider="instance"` row from sending rather than
silently rerouting it.

## Inline (`cid:`) body images — the one thing the transports disagree about

An invoice mail wants its payment QR *in* the letter (epic #269), and each provider expresses
that differently. `EmailAttachment.inline` is the one flag; `senders.py` maps it per transport:

| provider | mechanism | the HTML writes |
|---|---|---|
| `smtp` | the image is `add_related`-ed onto the **html part**, which turns that part into `multipart/related`; ordinary attachments stay at the top level in `multipart/mixed` | `cid:invoice-qr.png` |
| `sendgrid` | the same `attachments` array, with `"disposition": "inline"` and `"content_id"` | `cid:<content_id>` |
| `smtp2go` | a **separate top-level `inlines` array** (same `{filename, fileblob, mimetype}` entry shape as `attachments`) | `cid:<filename>` |
| `brevo` | **none.** Its attachment object is `{url, content, name}` — no Content-ID at all | not supported |

Three rules follow, and none of them is optional:

- **The content id is the filename.** SMTP2GO has no id field, so there the filename *is* the
  cid; the other two are made to agree with it, and that is the only way one composed fragment
  travels unchanged over all three. A filename is an identity — short, ASCII, unique in the
  message.
- **Ask `supports_inline_images(provider)` before composing the `<img>`.** The composer is the
  only layer that can pick a fallback (a plain pay link instead of a QR); discovering the
  failure after sending means a broken-image box in a client's inbox. Unknown names — including
  `"instance"`, which is a settings choice and not a transport — answer `False`.
- **An unsupported transport drops the part, never downgrades it.** A bare QR paperclipped to
  the bottom of an invoice mail, next to the box where it should have rendered, is worse than
  no QR. Logged once per process, not once per mail.

`cid:` is in the template sanitiser's `_URL_SCHEMES`, so a tenant may place the image in their
own body: it addresses a part of this very message rather than the network, and cannot report
an open back the way a remote `<img>` can. `data:` and everything else stay out.

## The two layers

**Content** is a *fragment* — paragraphs, a CTA button, a short list. It is built per mail
(`templates.branded_default_html`, `render.email_fragment`, or promoted plaintext) and runs
through `sanitize_email_html` whenever anything in it is not our own literal: tenant template
bodies, substituted variables, signatures. Sanitised on write *and* on send.

## Which mails a tenant may rewrite (`core/email/kinds.py`)

A **customisable kind** is a spec, and a module contributes its own the way it contributes
panels and permissions (§6): `ModuleDescriptor.email_templates`. Core declares core's — `reset`
and `invite` — and holds no module list. #161 shipped the editor with the kind list, the catalog
keys (`auth.email.{kind}_*`) and one global variable set all hardcoded, which is why the three
mails an agency's *clients* actually read (invoice, quote, reminder) were the only outgoing text
nobody could reword.

Each spec names its own `subject_key` / `body_key`, its own `variables`, an optional
`button_key`, and an async `sample(ctx, locale)` for the editor's test send — those are exactly
the things that are *not* shared between "reset your password" and "invoice 2026-0142 for
€ 1.210,00". Rules:

- **A key is stored data** (`org_email_templates.kind`), so it is unique across core and every
  module, and a module's keys are namespaced by the module (`invoicing.invoice`). Both are
  asserted at mount time (`validate_email_kinds`, called from `main.create_app`) — a build
  break, because the failure is invisible until one module's override resolves to another's
  mail. Core keeps the bare `reset`/`invite` it already shipped rows under.
- **The editor offers the kinds of the modules this org runs**, resolved from
  `org_settings.enabled_modules`; the write path validates against the same list, so a stale
  form cannot store an override for a mail this org no longer sends.
- **Every declared variable always resolves**, to `""` if need be. An unfilled marker reaches
  the inbox as a literal `{reference}`.
- **…and a variable that resolves to nothing takes its line with it** (epic #269). Some
  markers are genuinely optional: the invoice mail's `{link}` is a pay button, and there is no
  button when no payment provider is connected or the invoice is already settled. The two
  naive renderings are both wrong in front of a client — an empty `<p></p>` opening a gap in
  the middle of the letter, or a perfectly styled CTA whose `href` is the empty string, which
  navigates to the mail client's own idea of nowhere. So `branded_default_html` drops a line
  that renders blank, drops a paragraph left with no lines, and draws the button only when it
  has a URL; and `_tidy` collapses the hole the same absence leaves in the plaintext half. Put
  an optional marker in **a paragraph of its own** and the mail degrades to exactly the mail
  it was before the variable existed.
- **Adding a kind adds no schema**: it is a row in a table that already exists, and a missing
  row still means "use the built-in default", so shipping one changes nothing until a tenant
  types in the box.

**Chrome** is the outer document — `core/email/branding.py`. It wraps the fragment in the
tenant's branding at the send seam (`send_org_email`), *after* the org signature is appended,
so every mail gets it with no per-caller code. It contains `<html>`/`<body>` and therefore
**never passes the sanitiser**; everything interpolated into it is escaped or validated
instead (hex-checked colors, escaped brand name, http(s)-only logo URL). Wrapping is
idempotent: a body that already starts with `<!doctype` is left alone.

Tier precedence is unchanged from #161: a tenant override (Instellingen → E-mail) wins over the
built-in default body; both get the chrome. A **document mail's covering note** — the free text
the sender typed in the send dialog — leads *both* parts: escaped paragraphs before the HTML
fragment, plain text before the plaintext body. Prepending it to the text alone would make the
branded half of the mail quietly drop a sentence the client was meant to read.

## Branding resolution

`EmailBrand` (`load_brand` / `brand_from`) reads `org_settings.brand_name`,
`show_brand_name`, `logo_url`, `primary_color`, plus the org's own base URL
(`org_base_url`). Rules:

- **Never `org.name` in a mail** — that is the internal name; the displayed brand is
  `org_settings.brand_name` (Golden Rule 4).
- A relative `logo_url` is absolutised onto the org's host; any scheme but http(s) is
  dropped (e-mail clients block `data:`, and `javascript:` must never reach an `src`).
- A color goes into an unsanitised style attribute, so only `#hex` literals pass
  (`_safe_color`); anything else falls back to the model default.
- Brand resolution failing must **never block a mail** — send unwrapped instead.

## HTML e-mail rules (why the markup looks like 2005)

E-mail clients are not browsers. Gmail clips large mails, strips `<style>` in many contexts;
Outlook desktop renders with Word. Hence, in any fragment or chrome:

1. **Tables, not flexbox/grid.** Layout is nested `<table>` elements with `align`/`width`
   attributes. No `<div>`-based layout.
2. **Inline styles only.** No `<style>` blocks, no external stylesheets, no classes. Set
   `font-family` on every text-bearing element — inheritance is unreliable.
3. **600 px content width**, as both `width="600"` and `style="width:600px;max-width:100%"`.
4. **Fonts:** the `Arial,Helvetica,sans-serif` stack (`FONT_STACK`). No webfonts, and no
   quoted font names — quoting inside style attributes trips the sanitiser and some clients.
5. **Buttons** are a padded `<td>` with `background-color` and an inline-block `<a>` —
   never an image, no VML. See `button_html`.
6. **Images:** absolute `https` URLs, `alt` text, explicit `height`, `border:0`. No `data:`
   URIs (blocked by most clients). The logo is the only image the chrome ships; a *body*
   image travels as an inline `cid:` part instead (see above), never as a `data:` URI.
7. **Colors** are hex literals; the only dynamic one is the tenant's validated
   `primary_color`. Assume light background — no dark-mode variants (clients that force
   dark recolor themselves).
8. **Multipart always.** The `text` part is composed first and stands alone — every link a
   mail promises must be in it verbatim. HTML is an enhancement, never the only carrier
   (`OutgoingEmail.text` is required for exactly this reason).
9. **A preheader** (hidden first line, from the text part) is added by the chrome so inbox
   previews read sensibly.

## Copy rules

- Every string from the shared catalogs (`messages/en.json` + `nl.json`, same commit,
  `i18n:check` green). Locale: recipient's → org default → `nl`. No ICU plurals server-side
  (`app/i18n.py` does plain `{param}` substitution) — pick wording that works with a number
  in it, and special-case count == 1 in code if needed (the digest does).
- Dates print European (`dd-mm-yyyy`), notification sentences reuse the same
  `notifications.event.*` keys as the in-app feed (`modules/notifications/render.py` is the
  server twin of the web's `format.ts` — change them together).
- Dutch copy avoids em dashes (docs/UX.md).

## Adding a new outgoing mail — checklist

0. Decide whether the tenant may reword it. A mail their **client** reads almost always yes:
   declare an `EmailTemplateKind` on the module descriptor (`<module>.<name>` key, its own
   variables, a `sample` for the preview) and compose through `resolve_template` +
   `build_email_content`. An internal or transactional mail can stay tier 1.
1. Compose subject + plaintext from catalog keys (`en` + `nl`).
2. Build an HTML fragment if the mail deserves structure (button, list); otherwise plain
   text is fine — the seam promotes it to branded paragraphs automatically.
3. Send through `send_org_email(session, org_id, message)`; pass `brand=` if you already
   loaded it. Only bypass the seam for a hard technical reason, and then call
   `apply_branding` yourself.
4. Sanitise anything non-literal in the fragment; escape values before substitution.
5. Test: capture at the provider seam (`app.core.email.service.send_email`) and assert on
   both parts; a layout change deserves a look in a real client (Gmail + Outlook), not just
   a browser — that is what the template/channel test-send buttons are for.
