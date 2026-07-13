/**
 * Markdown → sanitized HTML, the render half of issue #66 (docs/UX.md "Long-form text").
 *
 * Long-form user text is stored as markdown *source* (never pre-rendered HTML) and rendered here.
 * This module and `Markdown.svelte` are the **only** place markup becomes markup — the single
 * audited `{@html}` site — so every rendered string passes through DOMPurify first. The API also
 * strips raw HTML on write (`app/core/richtext.py`); this is the authoritative boundary, that is
 * defence-in-depth.
 *
 * Browser-only: DOMPurify needs a DOM, so `renderMarkdown` is called only after mount (see
 * `Markdown.svelte`, which renders the escaped source during SSR/no-JS). Importing the module on
 * the server is harmless — nothing runs until `renderMarkdown` is called.
 */
import DOMPurify from "dompurify";
import { marked, type Tokens } from "marked";

import { sourceHref } from "$lib/core/ai";

// A deliberately small allow-list: the tags markdown itself produces, and nothing else. No
// `<img>` (no remote content / tracking pixels in a note), no `<h1>`/`<h2>` (headings in a task
// description are visual noise — `###`+ still render, as `<h3>`). Links are the one attribute
// surface, locked to safe protocols by DOMPurify and hardened further in the hook below.
const ALLOWED_TAGS = [
  "p",
  "br",
  "strong",
  "em",
  "del",
  "s",
  "blockquote",
  "code",
  "pre",
  "ul",
  "ol",
  "li",
  "a",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  // The @mention chip (issue #63): a fixed-class span this module's own extension emits.
  "span",
];
const ALLOWED_ATTR = ["href", "title", "class", "data-user-id", "data-contact-id"];

const _UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
// Optional kind prefix (#165): `mention:contact:<uuid>`; absent = a colleague (pre-#165 bodies).
const _MENTION_RE = new RegExp(`^@\\[([^\\]]+)\\]\\(mention:(?:(user|contact):)?(${_UUID})\\)`);
// AI answers cite records as `[Name](crm://<type>/<id>)` (epic #131): the type/id resolve to the
// app route here, so the model never has to know web paths and a bad reference degrades to text.
const _CRM_RE = new RegExp(`^\\[([^\\]]+)\\]\\(crm://([a-z_]+)/(${_UUID})\\)`);

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

let configured = false;

function ensureConfigured(): void {
  if (configured) return;
  // Every link opens in a new tab and can never reach back into the app (`noopener`) nor pass a
  // referrer or link-equity (`noreferrer nofollow`) — the content is user-authored. The one
  // exception: site-relative hrefs (the resolved crm:// references below) stay same-tab, they
  // *are* the app.
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      if ((node.getAttribute("href") ?? "").startsWith("/")) return;
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer nofollow");
    }
  });
  // Render `@[Name](mention:<uuid>)` markers as a distinguishable chip (issue #63). A marked
  // extension (not raw-HTML injection) keeps the "markdown source only" rule; the output is a
  // fixed-class span with an escaped display name and a UUID-shaped id, so it survives DOMPurify.
  marked.use({
    extensions: [
      {
        name: "mention",
        level: "inline",
        start(src: string) {
          const i = src.indexOf("@[");
          return i < 0 ? undefined : i;
        },
        tokenizer(src: string) {
          const m = _MENTION_RE.exec(src);
          if (m) return { type: "mention", raw: m[0], name: m[1], kind: m[2] ?? "user", id: m[3] };
        },
        renderer(token: Tokens.Generic) {
          const name = escapeHtml(String(token.name ?? ""));
          const id = String(token.id ?? "");
          // A contact chip reads differently from a colleague chip (#165) — both fixed-class
          // spans with a UUID-shaped id, so they survive DOMPurify.
          if (token.kind === "contact") {
            return `<span class="mention mention-contact" data-contact-id="${id}">@${name}</span>`;
          }
          return `<span class="mention" data-user-id="${id}">@${name}</span>`;
        },
      },
      {
        name: "crmlink",
        level: "inline",
        start(src: string) {
          const i = src.indexOf("[");
          return i < 0 ? undefined : i;
        },
        tokenizer(src: string) {
          const m = _CRM_RE.exec(src);
          if (m) return { type: "crmlink", raw: m[0], label: m[1], kind: m[2], id: m[3] };
        },
        renderer(token: Tokens.Generic) {
          const label = escapeHtml(String(token.label ?? ""));
          const href = sourceHref({
            type: String(token.kind ?? ""),
            id: String(token.id ?? ""),
            label: "",
          });
          // An unknown type is a hallucinated reference: show the words, link nothing.
          if (!href) return label;
          return `<a href="${href}" class="underline decoration-dotted underline-offset-2">${label}</a>`;
        },
      },
    ],
  });
  configured = true;
}

/** Render trusted-to-be-source markdown to sanitized HTML. Browser-only (needs a DOM). */
export function renderMarkdown(source: string): string {
  ensureConfigured();
  // `gfm` for tables-of-nothing/strikethrough/autolinks; `breaks` so a single newline is a line
  // break (users write notes, not prose — they expect Enter to break the line).
  const html = marked.parse(source, { async: false, gfm: true, breaks: true }) as string;
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    // Belt-and-suspenders on link protocols; DOMPurify blocks `javascript:` by default anyway.
    // A single leading `/` (never `//`, which is protocol-relative) admits the app's own routes —
    // the resolved crm:// references; a scheme can't hide in a path-relative URL.
    ALLOWED_URI_REGEXP: /^(?:https?|mailto|tel):|^\/(?!\/)/i,
  });
}
