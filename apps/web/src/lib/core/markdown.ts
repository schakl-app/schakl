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
import { marked } from "marked";

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
];
const ALLOWED_ATTR = ["href", "title"];

let configured = false;

function ensureConfigured(): void {
  if (configured) return;
  // Every link opens in a new tab and can never reach back into the app (`noopener`) nor pass a
  // referrer or link-equity (`noreferrer nofollow`) — the content is user-authored.
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer nofollow");
    }
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
    ALLOWED_URI_REGEXP: /^(?:https?|mailto|tel):/i,
  });
}
