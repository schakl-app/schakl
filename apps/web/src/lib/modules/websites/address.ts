/**
 * Reading an address the way somebody types it into the website form's domain picker.
 *
 * The API owns the rule for what a website is called (`app/core/webaddress.py`: `label`, `host`,
 * `url` come resolved on every row, and `path` is normalised on the way in). This is the one
 * browser-side convenience the form needs *before* anything is posted: when the typed text is
 * `breik.dev/briellaerd` or `https://www.klant.nl/shop/`, the domain quick-create gets the host
 * and the path box gets the rest — instead of the domain normaliser silently stripping the path
 * and the person ending up with a root site on the wrong client.
 */
export interface TypedAddress {
  /** The apex, lowercased, without scheme, credentials, port or `www.`. */
  host: string;
  /** Whether the text named the `www.` host. */
  www: boolean;
  /** The path as typed, trimmed of slashes at both ends — empty for the root. */
  path: string;
}

export function splitTypedAddress(raw: string): TypedAddress {
  let text = raw.trim();
  if (text.includes("://")) text = text.slice(text.indexOf("://") + 3);
  text = text.split("?")[0].split("#")[0];
  const slash = text.indexOf("/");
  let host = slash === -1 ? text : text.slice(0, slash);
  const path = slash === -1 ? "" : text.slice(slash + 1);
  if (host.includes("@")) host = host.slice(host.lastIndexOf("@") + 1);
  host = host
    .split(":")[0]
    .trim()
    .toLowerCase()
    .replace(/^\.+|\.+$/g, "");
  let www = false;
  if (host === "www") host = "";
  else if (host.startsWith("www.")) {
    host = host.slice(4);
    www = true;
  }
  return { host, www, path: path.replace(/^\/+|\/+$/g, "") };
}
