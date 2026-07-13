// Cloudflare Pages Function — POST /api/forms
//
// Handles every CMS-defined form (src/data/forms/*.json). It verifies the Cloudflare Turnstile
// token, then relays the submission by email through the SMTP2GO API, and 303-redirects back to
// the form page with ?sent=1 so the success banner shows. See apps/site/FORMS.md.
//
// NOT WIRED YET: the site currently builds as a static bundle. To activate, deploy apps/site on
// Cloudflare Pages (which runs this functions/ directory) and set the env/secrets below — or run
// the same logic as a standalone Worker and point the form `action` at it. No code here needs to
// change to turn the forms on; it is gated entirely on configuration.

interface Env {
  TURNSTILE_SECRET_KEY: string; // Turnstile secret (pairs with the public site key in the CMS)
  SMTP2GO_API_KEY: string; // SMTP2GO API key
  FORM_SENDER: string; // a verified SMTP2GO sender, e.g. "schakl website <website@breik.nl>"
  FORM_RECIPIENT: string; // where submissions land, e.g. "team@breik.nl"
}

const TURNSTILE_VERIFY = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';
const SMTP2GO_SEND = 'https://api.smtp2go.com/v3/email/send';

// Only these form slugs may be submitted, mapped to their page path. The recipient is fixed
// server-side (never taken from the request) so a form can't be turned into an open relay.
const FORMS: Record<string, { path: string }> = {
  contact: { path: '/contact' },
  interest: { path: '/interest' },
};

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  const form = await request.formData();
  const slug = String(form.get('_form') ?? '');
  const locale = String(form.get('_locale') ?? 'nl') === 'en' ? 'en' : 'nl';
  const known = FORMS[slug];
  if (!known) return new Response('Unknown form', { status: 400 });

  // 1) Verify Turnstile
  const token = String(form.get('cf-turnstile-response') ?? '');
  const ip = request.headers.get('CF-Connecting-IP') ?? '';
  const verify = await fetch(TURNSTILE_VERIFY, {
    method: 'POST',
    body: new URLSearchParams({ secret: env.TURNSTILE_SECRET_KEY, response: token, remoteip: ip }),
  }).then((r) => r.json<{ success: boolean }>());
  if (!verify.success) return new Response('Verification failed', { status: 400 });

  // 2) Build the email body from the visible fields (skip metadata + the Turnstile token)
  const rows: string[] = [];
  let replyTo = '';
  for (const [k, v] of form.entries()) {
    if (k.startsWith('_') || k === 'cf-turnstile-response') continue;
    if (k === 'email') replyTo = String(v);
    rows.push(`${k}: ${v}`);
  }

  // 3) Send via SMTP2GO
  const res = await fetch(SMTP2GO_SEND, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Smtp2go-Api-Key': env.SMTP2GO_API_KEY },
    body: JSON.stringify({
      sender: env.FORM_SENDER,
      to: [env.FORM_RECIPIENT],
      subject: `[schakl · ${slug}] nieuw bericht`,
      text_body: rows.join('\n'),
      custom_headers: replyTo ? [{ header: 'Reply-To', value: replyTo }] : undefined,
    }),
  });
  if (!res.ok) return new Response('Could not send', { status: 502 });

  // 4) Back to the form page with the success flag
  const base = locale === 'en' ? `/en${known.path}` : known.path;
  return Response.redirect(new URL(`${base}/?sent=1`, request.url).toString(), 303);
};

// A stray GET (e.g. someone visiting /api/forms) shouldn't 500.
export const onRequestGet: PagesFunction = () => new Response('Method not allowed', { status: 405 });
