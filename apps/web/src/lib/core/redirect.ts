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
