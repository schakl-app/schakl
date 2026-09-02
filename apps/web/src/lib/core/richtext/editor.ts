/**
 * The WYSIWYG half of the shared markdown editor (issue #255). Loaded lazily from
 * `RichTextEditor.svelte` so the ProseMirror bundle never weighs on first paint — SSR and
 * no-JS keep the plain textarea, and this module swaps in after hydration.
 *
 * The stored value stays **markdown source** (issue #66's rule): this editor is only a *view*
 * over it. Parsing reuses `renderMarkdown` — the one audited markdown→HTML path, DOMPurify
 * included — and serialization writes back the house conventions (`**` bold, `_` italic,
 * `- ` bullets, `@[Name](mention:…)` markers, plain-newline hard breaks because the renderer
 * runs `breaks: true`). Everything downstream — `sanitize_markdown`, the `Markdown` renderer,
 * PDF/UBL flattening — is untouched by construction.
 *
 * h1/h2 stay out of the schema (`levels: [3…6]`), mirroring the renderer that strips them:
 * what cannot be rendered must not be authorable.
 */
import { Editor, Extension, Node } from "@tiptap/core";
import Link from "@tiptap/extension-link";
import { PluginKey } from "@tiptap/pm/state";
import { Placeholder } from "@tiptap/extensions";
import StarterKit from "@tiptap/starter-kit";
import { Suggestion } from "@tiptap/suggestion";
import { MarkdownSerializer } from "prosemirror-markdown";
import type { Node as PMNode } from "@tiptap/pm/model";

import { renderMarkdown } from "$lib/core/markdown";
import { clampImageWidth, fileImageMarkdown } from "$lib/core/richtext/images";

export interface Candidate {
  id: string;
  name: string;
  kind?: "user" | "contact" | "task";
  subtitle?: string;
  /** Task extras (#237), shown in the dropdown: who holds it and when it's due. */
  assignee?: string;
  due?: string;
  overdue?: boolean;
}

/** What the Svelte component receives to drive its own dropdown — the plugin never renders. */
export interface SuggestState {
  trigger: "@" | "#";
  items: Candidate[];
  command: (item: Candidate) => void;
}

export interface RichTextBridge {
  onSuggestOpen: (state: SuggestState) => void;
  onSuggestUpdate: (state: SuggestState) => void;
  /** Return true when the key was consumed (arrows/enter/escape while the dropdown is open). */
  onSuggestKey: (event: KeyboardEvent) => boolean;
  onSuggestClose: () => void;
}

// --- mention / #task nodes ----------------------------------------------------
// Inline atoms mirroring the renderer's output (core/markdown.ts), so `renderMarkdown` HTML
// parses straight back into them and `renderHTML` round-trips through the same shapes.

const MentionNode = Node.create({
  name: "mention",
  group: "inline",
  inline: true,
  atom: true,
  selectable: false,

  addAttributes() {
    return {
      id: { default: "" },
      label: { default: "" },
      kind: { default: "user" },
    };
  },

  parseHTML() {
    return [
      {
        tag: "span.mention",
        // Above the link mark's `a[href]` rule so a chip never half-parses as styling.
        priority: 100,
        getAttrs: (el) => {
          const contact = el.getAttribute("data-contact-id");
          const user = el.getAttribute("data-user-id");
          if (!contact && !user) return false;
          return {
            id: contact || user,
            kind: contact ? "contact" : "user",
            label: (el.textContent ?? "").replace(/^@/, ""),
          };
        },
      },
    ];
  },

  renderHTML({ node }) {
    const attrs =
      node.attrs.kind === "contact"
        ? { class: "mention mention-contact", "data-contact-id": node.attrs.id }
        : { class: "mention", "data-user-id": node.attrs.id };
    return ["span", attrs, `@${node.attrs.label}`];
  },

  renderText({ node }) {
    return `@${node.attrs.label}`;
  },
});

const TaskrefNode = Node.create({
  name: "taskref",
  group: "inline",
  inline: true,
  atom: true,
  selectable: false,

  addAttributes() {
    return {
      id: { default: "" },
      label: { default: "" },
    };
  },

  parseHTML() {
    return [
      {
        tag: "a.mention-task",
        // Must beat the link mark's generic `a[href]` rule.
        priority: 100,
        getAttrs: (el) => {
          const match = /^\/tasks\/([0-9a-fA-F-]{36})$/.exec(el.getAttribute("href") ?? "");
          if (!match) return false;
          return { id: match[1], label: (el.textContent ?? "").replace(/^#/, "") };
        },
      },
    ];
  },

  renderHTML({ node }) {
    return [
      "a",
      { class: "mention mention-task", href: `/tasks/${node.attrs.id}` },
      `#${node.attrs.label}`,
    ];
  },

  renderText({ node }) {
    return `#${node.attrs.label}`;
  },
});

const FileImageNode = Node.create({
  name: "fileimage",
  group: "inline",
  inline: true,
  atom: true,
  // Selectable on purpose (unlike the mention chips): selecting the image is how the width
  // controls know which one you mean, and Backspace-to-delete needs a node selection.
  selectable: true,
  draggable: true,

  addAttributes() {
    return {
      id: { default: "" },
      alt: { default: "" },
      /** Percent of the text column, or null for natural size (see richtext/images.ts). */
      width: { default: null },
    };
  },

  parseHTML() {
    return [
      {
        tag: "img[src]",
        getAttrs: (el) => {
          // Only the marker's own render parses back — an <img> naming anything but our own
          // file store is not authorable here, exactly as it is not renderable.
          const m = /^\/api\/v1\/files\/([0-9a-fA-F-]{36})$/.exec(el.getAttribute("src") ?? "");
          if (!m) return false;
          return {
            id: m[1],
            alt: el.getAttribute("alt") ?? "",
            width: clampImageWidth(el.getAttribute("data-width") ?? ""),
          };
        },
      },
    ];
  },

  renderHTML({ node }) {
    const attrs: Record<string, string> = {
      src: `/api/v1/files/${node.attrs.id}`,
      alt: String(node.attrs.alt ?? ""),
    };
    const width = clampImageWidth(node.attrs.width);
    if (width !== null) {
      attrs["data-width"] = String(width);
      attrs.style = `width:${width}%`;
    }
    return ["img", attrs];
  },

  renderText({ node }) {
    return String(node.attrs.alt ?? "");
  },
});

/**
 * Upload image files and insert their markers — the one routine behind the paste, the drop
 * and the toolbar button, so all three arrive as the same node with the same name. Uploads
 * run sequentially (they share one connection anyway) and whatever landed is inserted in one
 * chain: a refused file costs its own upload, never the batch.
 */
export async function insertUploadedImages(
  editor: Editor,
  files: File[],
  upload: (file: File) => Promise<{ id: string; alt: string } | null>,
  pos?: number,
): Promise<void> {
  const nodes: { type: "fileimage"; attrs: { id: string; alt: string; width: null } }[] = [];
  for (const file of files) {
    const stored = await upload(file);
    if (stored) {
      nodes.push({ type: "fileimage", attrs: { id: stored.id, alt: stored.alt, width: null } });
    }
  }
  if (nodes.length === 0 || editor.isDestroyed) return;
  if (pos !== undefined) {
    editor.chain().insertContentAt(Math.min(pos, editor.state.doc.content.size), nodes).run();
  } else {
    editor.chain().focus().insertContent(nodes).run();
  }
}

// --- suggestion wiring ---------------------------------------------------------
// One plugin per trigger; both hand their state to the component through the bridge, which
// owns the dropdown (anchored under the editor — a phone reaches it without a caret-precise
// popover, same decision as the textarea era).

function suggestionPlugins(
  editor: Editor,
  trigger: "@" | "#",
  nodeName: "mention" | "taskref",
  items: () => Candidate[],
  bridge: RichTextBridge,
) {
  return Suggestion<Candidate>({
    editor,
    char: trigger,
    pluginKey: new PluginKey(`suggest-${nodeName}`),
    items: ({ query }) =>
      items()
        .filter((m) => m.name.toLowerCase().includes(query.toLowerCase()))
        .slice(0, 6),
    command: ({ editor: ed, range, props }) => {
      const attrs =
        nodeName === "mention"
          ? { id: props.id, label: props.name, kind: props.kind === "contact" ? "contact" : "user" }
          : { id: props.id, label: props.name };
      ed.chain()
        .focus()
        .insertContentAt(range, [
          { type: nodeName, attrs },
          { type: "text", text: " " },
        ])
        .run();
    },
    render: () => ({
      onStart: (props) =>
        bridge.onSuggestOpen({
          trigger,
          items: props.items,
          command: (item) => props.command(item),
        }),
      onUpdate: (props) =>
        bridge.onSuggestUpdate({
          trigger,
          items: props.items,
          command: (item) => props.command(item),
        }),
      onKeyDown: ({ event }) => bridge.onSuggestKey(event),
      onExit: () => bridge.onSuggestClose(),
    }),
  });
}

interface SuggestOptions {
  people: () => Candidate[];
  tasks: () => Candidate[];
  bridge: RichTextBridge;
}

const SuggestExtension = Extension.create<SuggestOptions>({
  name: "richtextSuggest",

  addProseMirrorPlugins() {
    // Both triggers always register — a surface without candidates just never shows items,
    // and a list that arrives after mount still works because `items` reads the live prop.
    return [
      suggestionPlugins(this.editor, "@", "mention", this.options.people, this.options.bridge),
      suggestionPlugins(this.editor, "#", "taskref", this.options.tasks, this.options.bridge),
    ];
  },
});

// --- markdown serialization ----------------------------------------------------
// prosemirror-markdown's serializer with our node/mark names and house conventions. The
// escaping rules (literal `*`/`[` in prose, intra-word underscores left alone) are the
// battle-tested part we deliberately did not reinvent.

function backticksFor(node: PMNode, side: -1 | 1): string {
  const ticks = /`+/g;
  let len = 0;
  if (node.isText) {
    for (let m = ticks.exec(node.text ?? ""); m; m = ticks.exec(node.text ?? "")) {
      len = Math.max(len, m[0].length);
    }
  }
  let result = len > 0 && side > 0 ? " `" : "`";
  for (let i = 0; i < len; i += 1) result += "`";
  if (len > 0 && side < 0) result += " ";
  return result;
}

const serializer = new MarkdownSerializer(
  {
    paragraph(state, node) {
      state.renderInline(node);
      state.closeBlock(node);
    },
    heading(state, node) {
      state.write(`${"#".repeat(node.attrs.level)} `);
      state.renderInline(node, false);
      state.closeBlock(node);
    },
    bulletList(state, node) {
      state.renderList(node, "  ", () => "- ");
    },
    orderedList(state, node) {
      const start = node.attrs.start || 1;
      const maxWidth = String(start + node.childCount - 1).length;
      const space = state.repeat(" ", maxWidth + 2);
      state.renderList(node, space, (i) => {
        const nStr = String(start + i);
        return `${state.repeat(" ", maxWidth - nStr.length)}${nStr}. `;
      });
    },
    listItem(state, node) {
      state.renderContent(node);
    },
    blockquote(state, node) {
      state.wrapBlock("> ", null, node, () => state.renderContent(node));
    },
    codeBlock(state, node) {
      state.write("```\n");
      state.text(node.textContent, false);
      state.ensureNewLine();
      state.write("```");
      state.closeBlock(node);
    },
    horizontalRule(state, node) {
      state.write("---");
      state.closeBlock(node);
    },
    // A plain newline, not markdown's backslash form: the renderer runs `breaks: true`, and
    // the flatteners (`markdown_to_plaintext`) must never meet a stray trailing backslash.
    hardBreak(state, node, parent, index) {
      for (let i = index + 1; i < parent.childCount; i += 1) {
        if (parent.child(i).type !== node.type) {
          state.write("\n");
          return;
        }
      }
    },
    text(state, node) {
      state.text(node.text ?? "");
    },
    mention(state, node) {
      const prefix = node.attrs.kind === "contact" ? "contact:" : "";
      state.write(`@[${node.attrs.label}](mention:${prefix}${node.attrs.id})`);
    },
    taskref(state, node) {
      state.write(`#[${node.attrs.label}](mention:task:${node.attrs.id})`);
    },
    fileimage(state, node) {
      // `![alt](file:<uuid> =50%)` — the renderer's own marker (richtext/images.ts), so what
      // the editor shows and what the reading view draws are one grammar.
      state.write(fileImageMarkdown(node.attrs.id, String(node.attrs.alt ?? ""), node.attrs.width));
    },
  },
  {
    bold: { open: "**", close: "**", mixable: true, expelEnclosingWhitespace: true },
    italic: { open: "_", close: "_", mixable: true, expelEnclosingWhitespace: true },
    strike: { open: "~~", close: "~~", mixable: true, expelEnclosingWhitespace: true },
    link: {
      open: "[",
      close: (_state, mark) => `](${String(mark.attrs.href ?? "")})`,
    },
    code: {
      open: (_state, _mark, parent, index) => backticksFor(parent.child(index), -1),
      close: (_state, _mark, parent, index) => backticksFor(parent.child(index - 1), 1),
      escape: false,
    },
  },
);

function serializeNode(doc: PMNode): string {
  const markdown = serializer.serialize(doc, { tightLists: true });
  return markdown.trim() === "" ? "" : markdown;
}

export function serializeDoc(editor: Editor): string {
  return serializeNode(editor.state.doc);
}

/** Markdown for a document range — the AI assist's selection scope. */
export function serializeRange(editor: Editor, from: number, to: number): string {
  return serializeNode(editor.state.doc.cut(from, to));
}

// --- factory --------------------------------------------------------------------

export interface CreateOptions {
  element: HTMLElement;
  /** Markdown source (with mention markers) — parsed through `renderMarkdown`. */
  content: string;
  placeholder?: string;
  people: () => Candidate[];
  tasks: () => Candidate[];
  bridge: RichTextBridge;
  onUpdate: (markdown: string) => void;
  /** Fired on every transaction — the component bumps a counter for toolbar active states. */
  onTransaction: () => void;
  /** A plain click landed on a link: the component opens its popover prefilled with the href.
   *  Clicking a blue label is the only discoverable way to reach a URL the text hides. */
  onLinkClick: (href: string) => void;
  /** Store a pasted/dropped/picked image against the host record and answer its file id —
   *  absent disables image handling entirely (an editor with no record to hang a file on).
   *  Busy and error states live inside this function, owned by the component. */
  uploadImage?: (file: File) => Promise<{ id: string; alt: string } | null>;
}

export function createRichTextEditor(options: CreateOptions): Editor {
  const instance: Editor = new Editor({
    element: options.element,
    extensions: [
      StarterKit.configure({
        // h1/h2 are stripped by the renderer, so they are not authorable either.
        heading: { levels: [3, 4, 5, 6] },
        // Markdown has no underline; keep the schema serializable.
        underline: false,
        // Registered separately below: the stock extension couples `inclusive` to `autolink`.
        link: false,
      }),
      // `autolink` makes the stock link mark *inclusive* — typing at the end of a link keeps
      // extending it, and there is no way out but the toolbar. Non-inclusive is the behaviour
      // people know from Notion/Slack: type after a link and you are writing plain text again.
      // Autolinking full URLs as they are typed still works — that plugin marks ranges itself.
      Link.extend({ inclusive: false }).configure({
        openOnClick: false,
        autolink: true,
        defaultProtocol: "https",
      }),
      Placeholder.configure({ placeholder: options.placeholder ?? "" }),
      MentionNode,
      TaskrefNode,
      FileImageNode,
      SuggestExtension.configure({
        people: options.people,
        tasks: options.tasks,
        bridge: options.bridge,
      }),
    ],
    // `images: true` unconditionally: a marker in a field whose *surface* draws no images must
    // still survive an edit round-trip — parsed to alt text it would be silently destroyed on
    // the next save. What a surface shows is the renderer's decision, never the serializer's.
    content: renderMarkdown(options.content, { images: true }),
    editorProps: {
      attributes: {
        class: "rt-prose",
        role: "textbox",
        "aria-multiline": "true",
      },
      // A plain left-click on a link opens the edit popover (prefilled) — the caret alone is
      // invisible feedback, and the toolbar button is not discoverable enough on its own.
      // Returning false keeps ProseMirror's own click handling (caret placement) intact.
      handleClick: (view, pos, event) => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey) return false;
        const $pos = view.state.doc.resolve(pos);
        const node = $pos.nodeAfter ?? $pos.nodeBefore;
        const mark = node?.marks.find((m) => m.type === view.state.schema.marks.link);
        if (mark) options.onLinkClick(String(mark.attrs.href ?? ""));
        return false;
      },
      // Pasting a screenshot uploads it and drops the image at the caret — only where the host
      // wired an upload target. A paste that also carries text is the text: copying from a web
      // page brings the words *and* a rendering of them, and the person meant the words (the
      // same rule FileAttachments applies at the document level).
      handlePaste: (_view, event) => {
        const upload = options.uploadImage;
        if (!upload) return false;
        const data = event.clipboardData;
        const files = Array.from(data?.files ?? []).filter((f) => f.type.startsWith("image/"));
        if (files.length === 0 || data?.types.includes("text/plain")) return false;
        event.preventDefault();
        void insertUploadedImages(instance, files, upload);
        return true;
      },
      // The same gesture minus the clipboard. `moved` is ProseMirror's own content being
      // dragged within the document — a reorder, not an upload.
      handleDrop: (view, event, _slice, moved) => {
        const upload = options.uploadImage;
        if (!upload || moved) return false;
        const files = Array.from(event.dataTransfer?.files ?? []).filter((f) =>
          f.type.startsWith("image/"),
        );
        if (files.length === 0) return false;
        event.preventDefault();
        const drop = view.posAtCoords({ left: event.clientX, top: event.clientY });
        void insertUploadedImages(instance, files, upload, drop?.pos);
        return true;
      },
    },
    onUpdate: ({ editor }) => options.onUpdate(serializeDoc(editor)),
    onTransaction: () => options.onTransaction(),
  });
  return instance;
}

export type { Editor };
