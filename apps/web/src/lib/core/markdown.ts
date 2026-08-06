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
// `<img>` by default (no remote content / tracking pixels in a note), no `<h1>`/`<h2>` (headings
// in a task description are visual noise — `###`+ still render, as `<h3>`). Links are the one
// attribute surface, locked to safe protocols by DOMPurify and hardened further in the hook below.
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
  // We parse with `gfm: true`, so markdown *does* produce these — and stripping the tags while
  // keeping their text turned a table into a run of loose words with the header row missing.
  // The API's own renderer (`richtext.markdown_to_html`, for documents) has always allowed them;
  // the two disagreeing was the bug, not the tags.
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  // The @mention chip (issue #63): a fixed-class span this module's own extension emits.
  "span",
];
const ALLOWED_ATTR = ["href", "title", "class", "data-user-id", "data-contact-id"];

/** The only `src` an `<img>` may carry: a file this instance stored and serves. */
const FILE_SRC = "/api/v1/files/";

const _UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
// Optional kind prefix (#165): `mention:contact:<uuid>`; absent = a colleague (pre-#165 bodies).
const _MENTION_RE = new RegExp(`^@\\[([^\\]]+)\\]\\(mention:(?:(user|contact):)?(${_UUID})\\)`);
// A #task reference (#197): same marker family, `#` trigger, its own extension so the existing
// @-mention rendering stays byte-for-byte unchanged. Renders as a *deep link* to the task —
// an `<a href="/tasks/<id>">` (the crm:// route mapping), not a data-* span like a person chip.
const _TASKREF_RE = new RegExp(`^#\\[([^\\]]+)\\]\\(mention:task:(${_UUID})\\)`);
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
// `marked`'s configuration is process-wide (see `ensureConfigured`), so the per-call image
// choice reaches the renderer through here. Rendering is synchronous, so there is no window
// in which a second call could see the wrong value.
let allowImages = false;

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
    // An `<img>` may only ever name a file we stored and serve ourselves. The `fileimage`
    // extension is the only thing that emits one, but *this* is where it is enforced: a
    // remote src is a request to somebody else's server, which for a received e-mail means
    // telling the sender the agency opened it. Removed outright rather than blanked — an
    // empty `<img>` is a broken-image icon in the middle of someone's message.
    if (node.tagName === "IMG" && !(node.getAttribute("src") ?? "").startsWith(FILE_SRC)) {
      node.remove();
    }
  });
  // Ordinary markdown images never render — `![x](https://…)` degrades to its alt text, whatever
  // the `images` option says. Only the `fileimage` extension below may produce an `<img>`, so
  // "an image is something we already hold" is true by construction rather than by allow-list.
  marked.use({
    renderer: {
      image(token: Tokens.Image) {
        return escapeHtml(token.text ?? "");
      },
    },
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
        name: "taskref",
        level: "inline",
        start(src: string) {
          const i = src.indexOf("#[");
          return i < 0 ? undefined : i;
        },
        tokenizer(src: string) {
          const m = _TASKREF_RE.exec(src);
          if (m) return { type: "taskref", raw: m[0], name: m[1], id: m[2] };
        },
        renderer(token: Tokens.Generic) {
          const name = escapeHtml(String(token.name ?? ""));
          const id = String(token.id ?? "");
          // A same-app relative href: the DOMPurify hook keeps it same-tab, and the URI
          // allow-list already admits a single leading `/`. Visually a chip, distinct from a
          // person mention (see Markdown.svelte `.mention-task`).
          return `<a href="/tasks/${id}" class="mention mention-task">#${name}</a>`;
        },
      },
      {
        name: "fileimage",
        level: "inline",
        start(src: string) {
          const i = src.indexOf("![");
          return i < 0 ? undefined : i;
        },
        tokenizer(src: string) {
          const m = _FILE_IMAGE.exec(src);
          if (m) return { type: "fileimage", raw: m[0], alt: m[1], id: m[2] };
        },
        renderer(token: Tokens.Generic) {
          const alt = escapeHtml(String(token.alt ?? ""));
          // Off by default, and the fallback is the alt text rather than nothing: a body that
          // says "Bureau" reads better than a body with a hole in it.
          if (!allowImages) return alt;
          const id = String(token.id ?? "");
          return `<img src="/api/v1/files/${id}" alt="${alt}" loading="lazy" />`;
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

/** `![alt](file:<uuid>)` — an image the API stored for us (an e-mail's `cid:` part). */
const _FILE_IMAGE = new RegExp(`^!\\[([^\\]]*)\\]\\(file:(${_UUID})\\)`);

export interface RenderOptions {
  /**
   * Draw `file:<uuid>` images. Off everywhere by default and on **only** for a received
   * e-mail body: a note has no business fetching pictures, and the marker is deliberately the
   * one image form that cannot name a remote host — the bytes are ours, already downloaded,
   * already de-duplicated. A remote `<img>` in a mail is a tracking pixel, and the API drops
   * those on the way in; this is the second half of the same rule.
   */
  images?: boolean;
}

/** Render trusted-to-be-source markdown to sanitized HTML. Browser-only (needs a DOM). */
export function renderMarkdown(source: string, options: RenderOptions = {}): string {
  ensureConfigured();
  allowImages = options.images === true;
  // `gfm` for tables-of-nothing/strikethrough/autolinks; `breaks` so a single newline is a line
  // break (users write notes, not prose — they expect Enter to break the line).
  const html = marked.parse(source, { async: false, gfm: true, breaks: true }) as string;
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: options.images ? [...ALLOWED_TAGS, "img"] : ALLOWED_TAGS,
    ALLOWED_ATTR: options.images ? [...ALLOWED_ATTR, "src", "alt", "loading"] : ALLOWED_ATTR,
    // Belt-and-suspenders on link protocols; DOMPurify blocks `javascript:` by default anyway.
    // A single leading `/` (never `//`, which is protocol-relative) admits the app's own routes —
    // the resolved crm:// references and file: images; a scheme can't hide in a path-relative URL.
    ALLOWED_URI_REGEXP: /^(?:https?|mailto|tel):|^\/(?!\/)/i,
  });
}
