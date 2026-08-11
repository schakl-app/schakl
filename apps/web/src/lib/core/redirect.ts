/**
 * "Where were you headed?", made safe to answer.
 *
 * A `?next=` travels in a URL anyone can write and a bookmark can carry, so it is an untrusted
 * string that we are about to hand to `redirect()` — the classic open-redirect shape, and a
 * genuinely useful one to an attacker here because it is reached *through the login screen*: a
 * link that signs someone in and then lands them on a look-alike host has borrowed this app's
 * credibility for the page that asks for their password again.
 *
 * So the answer is a whitelist of *shape*, not a blacklist of hosts. Only a same-document
 * absolute path survives:
 *
 * * `//evil.example` and `/\evil.example` are protocol-relative URLs — a browser reads both as
 *   another origin, and the second is why a backslash is refused rather than normalised.
 * * `https://evil.example` names a host outright, and fails the leading-slash test.
 * * A control character can split a `Location` header, so anything below U+0020 is refused.
 *
 * Anything that does not survive returns `null` and the caller falls back to its own default,
 * rather than 400-ing: a stale link is not the visitor's mistake, and refusing to sign them in
 * over one helps nobody.
 */
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001F\u007F]/;

export function safeInternalPath(
  raw: FormDataEntryValue | string | null | undefined,
): string | null {
  if (typeof raw !== "string") return null;
  const value = raw.trim();
  if (!value.startsWith("/")) return null;
  if (value.startsWith("//") || value.startsWith("/\\")) return null;
  if (CONTROL_CHARS.test(value)) return null;
  return value;
}

/**
 * The sign-in URL a guard turns an anonymous visitor away to, carrying where they were headed.
 *
 * There is one of these rather than an `encodeURIComponent` at each guard because the *reading*
 * side was complete long before anything wrote it: `/login` has parsed `?next=`, threaded it
 * through the 2FA step and landed on it since it was written, and the whole feature was missing
 * because the one line that redirects the anonymous visitor threw `event.url` away. A shared
 * producer is what stops the next guard from doing the same.
 *
 * The **fragment is deliberately absent**: it never reaches the server, so there is nothing here
 * to carry it with. A deep link into a tab or an anchor lands on the page and not the anchor —
 * the alternative is a client-side dance for the last few characters of the URL.
 *
 * `home` is where signing in lands by default, so a visitor turned away from *it* needs no
 * `next` at all: it would be a longer URL that changes nothing, and one more untrusted string
 * on the way back in.
 */
export function loginPath(url: URL, options?: { base?: string; home?: string }): string {
  const base = options?.base ?? "/login";
  const target = url.pathname + url.search;
  if (target === (options?.home ?? "/")) return base;
  return `${base}?next=${encodeURIComponent(target)}`;
}
