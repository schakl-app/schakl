/** Mirror of the API's `normalize_domain_name` (domains/schemas.py): what the user typed or
 * pasted, reduced to the bare root domain — scheme, credentials, port, path and a leading
 * "www." stripped, lowercased. The API stays authoritative; this is the form showing the
 * value that will actually be stored. */
export function normalizeDomainName(value: string): string {
  let name = value.trim().toLowerCase();
  if (name.includes("://")) name = name.slice(name.indexOf("://") + 3);
  for (const sep of ["/", "?", "#"]) {
    const at = name.indexOf(sep);
    if (at !== -1) name = name.slice(0, at);
  }
  const cred = name.lastIndexOf("@");
  if (cred !== -1) name = name.slice(cred + 1);
  const port = name.indexOf(":");
  if (port !== -1) name = name.slice(0, port);
  for (;;) {
    let stripped = name.replace(/^\.+|\.+$/g, "");
    if (stripped === "www") stripped = "";
    else if (stripped.startsWith("www.")) stripped = stripped.slice(4);
    if (stripped === name) return name;
    name = stripped;
  }
}

/** Mirror of the API's `tld_of` (domains/schemas.py): everything after the first label of a
 * normalized name ("example.co.uk" → "co.uk"), or null for a dotless name. Only used to show
 * the matching TLD price while typing — the API stamps the stored value. */
export function tldOf(name: string): string | null {
  const at = name.indexOf(".");
  return at === -1 ? null : name.slice(at + 1) || null;
}
