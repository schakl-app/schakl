# Site forms (contact, interest) — how they work and how to turn them on

> Internal note for contributors. The marketing forms are **CMS-driven and, for now, HTML
> only** — they render and validate in the browser but do not submit anywhere yet. This
> describes the pieces and the one-time wiring that makes them send email.

## What exists today

- **Definitions** live in `src/data/forms/*.json` (a CMS "Formulieren" collection). Each form
  has a `slug`, localized `title`/`intro`/`submitLabel`/`successMessage`, a `recipient` (for
  reference), and a `fields` list — each field a `name`, `type`
  (`text`/`email`/`tel`/`textarea`/`select`), `required`, a localized `label`, and `options`
  for selects. An editor can add fields or whole forms without touching code.
- **`FormRenderer.astro`** turns a definition into a plain `<form method="POST" action="/api/forms">`.
- **`FormPage.astro`** wraps it with the hero, a success banner (revealed on `?sent=1`) and, for
  contact, an aside with other ways to reach us.
- **Pages**: `/contact` and `/interest` (+ `/en/...`). Linked from the header (Contact) and the
  footer (Contact, Interesse).
- **Spam**: Cloudflare **Turnstile**. The widget renders only when a public site key is set in the
  CMS (`settings.forms.turnstileSiteKey`); the token is verified server-side (below). No honeypot.

Submitting today POSTs to `/api/forms`, which does not exist until the function is deployed — so
nothing is sent. That is intentional.

## The backend: one Cloudflare function → SMTP2GO

`functions/api/forms.ts` is a **Cloudflare Pages Function** for `POST /api/forms`. It:

1. Rejects unknown form slugs (the allow-list + fixed recipient keep it from being an open relay —
   the recipient is **never** taken from the request).
2. Verifies the Turnstile token via `siteverify`.
3. Builds the email from the visible fields (setting `Reply-To` to the submitter's `email`).
4. Sends it through the **SMTP2GO** send API.
5. `303`-redirects back to `/<form>/?sent=1` so the success banner shows.

### Turning it on (one-time)

1. **Turnstile**: create a Turnstile widget in the Cloudflare dashboard. Put the **site key**
   (public) in the CMS under *Site-instellingen → Formulieren*; keep the **secret key** for step 3.
2. **Hosting**: this function runs on **Cloudflare Pages**. Either deploy `apps/site` on Pages
   (it picks up `functions/` automatically), or, if the site keeps its current Docker/Traefik
   deploy, run the same handler as a standalone **Cloudflare Worker** and change the form `action`
   in `FormRenderer.astro` to the Worker URL. No handler code changes either way.
3. **Secrets / env** (Pages → Settings → Environment variables):
   - `TURNSTILE_SECRET_KEY` — pairs with the CMS site key.
   - `SMTP2GO_API_KEY` — from the SMTP2GO dashboard.
   - `FORM_SENDER` — a **verified** SMTP2GO sender, e.g. `schakl website <website@breik.nl>`.
   - `FORM_RECIPIENT` — where submissions land, e.g. `team@breik.nl`.
4. Add the form slug to the `FORMS` allow-list in `functions/api/forms.ts` if it is new.

That's it — with the site key set and the function deployed, the existing forms send email and show
their success banner.

## Adding a form

1. Create `src/data/forms/<slug>.json` (copy `contact.json`; set `slug`, `recipient`, `fields`).
2. Add routes `src/pages/<slug>.astro` and `src/pages/en/<slug>.astro` that render
   `<FormPage locale form={form} />`.
3. Link it (header `nav` / `footerLinks` in `settings/site.json`).
4. Add `<slug>` to the `FORMS` map in the function so it may submit.

## Notes

- Per-form recipients: today one `FORM_RECIPIENT` receives all forms (the subject carries the
  slug). To route per form, extend the `FORMS` map with a recipient and read it there.
- The success flow is redirect-based (no client JS to submit), so it degrades gracefully and needs
  no rebuild to go live.
