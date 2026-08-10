/**
 * Dropping a file onto an upload control (house convention — docs/UX.md).
 *
 * Every upload in the app was a click-to-browse file input and nothing else, which is the one
 * gesture people no longer reach for first: an attachment, a logo, a spreadsheet and a `.eml`
 * all arrive by being dragged out of a mail client or a folder. So the drop is added *once*,
 * here, rather than eleven times with eleven slightly different hover styles.
 *
 * The action deliberately drops onto the **input**, not past it: the files it accepts are
 * assigned to `input.files` and a bubbling `change` is dispatched, so whatever the control
 * already did on change — build a preview, name the file, `requestSubmit()` the multipart
 * form, POST a `FormData` — happens unchanged, and a form-posted upload really does carry the
 * bytes. A control gains drag-and-drop by wrapping its markup in `use:filedrop`; nothing about
 * how it uploads has to be known here, and nothing about it changes.
 *
 *   <div use:filedrop>
 *     <label>… <input type="file" accept="image/png" onchange={…} /></label>
 *   </div>
 *
 * It is an **accelerator, never the only path** (docs/UX.md's rule for drag-and-drop): the
 * button underneath keeps working, so a keyboard or touch user loses nothing. That is also why
 * a refused drop is quiet by default — the control it lands on is still there to be clicked.
 *
 * `accept` is honoured the way the native picker honours it, which is to say advisorily: a file
 * whose type or extension the input asked for goes through, a clearly wrong one is refused with
 * `errors.upload_type` (the host renders it), and a file the browser could not type at all is
 * let through for the server to judge — the native dialog's "All files" escape hatch does the
 * same, and every one of these uploads is validated server-side regardless. An input without
 * `multiple` takes the first file, matching what a browser does when you drop onto one directly.
 */
import type { Action } from "svelte/action";

export interface FileDropParams {
  /**
   * The input the files land on. Defaults to the first file input inside the node, resolved at
   * drop time so a conditionally rendered control still works. Pass a getter when the input
   * lives outside the drop target (a toolbar button's hidden input, say).
   */
  input?: HTMLInputElement | null | (() => HTMLInputElement | null | undefined);
  /** Nothing may be dropped — a disabled control, or a record in use mode. */
  disabled?: boolean;
  /** An i18n key for a refused drop, today only `errors.upload_type`. */
  onerror?: (key: string) => void;
}

/**
 * Does this file answer one of the input's `accept` tokens? Exported for its own test: the
 * whole reason a drop can be wrong where a click cannot is that the browser filtered the
 * dialog and nobody filters the desktop.
 */
export function acceptsFile(file: { name: string; type: string }, accept: string): boolean {
  const tokens = accept
    .split(",")
    .map((token) => token.trim().toLowerCase())
    .filter(Boolean);
  if (tokens.length === 0) return true;

  const name = file.name.toLowerCase();
  const type = file.type.toLowerCase();
  const matches = tokens.some((token) => {
    if (token.startsWith(".")) return name.endsWith(token);
    if (token.endsWith("/*")) return type.startsWith(token.slice(0, -1));
    return type === token;
  });
  // An untypeable file (no extension the OS knows) matches nothing and is not thereby wrong;
  // the server is the authority on what it will store.
  return matches || type === "";
}

/** The drag carries files, rather than a reordered task chip or a text selection. */
function carriesFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes("Files");
}

export const filedrop: Action<HTMLElement, FileDropParams | undefined> = (node, params) => {
  let current: FileDropParams = params ?? {};
  // Entering a child fires dragleave on the parent, so a plain boolean flickers the highlight
  // off over every icon and label inside the zone. Count the crossings instead.
  let depth = 0;

  node.dataset.filedrop = "";

  function resolveInput(): HTMLInputElement | null {
    const from = current.input;
    if (typeof from === "function") return from() ?? null;
    if (from) return from;
    return node.querySelector<HTMLInputElement>('input[type="file"]');
  }

  function highlight(on: boolean) {
    node.dataset.filedrop = on ? "over" : "";
    if (!on) depth = 0;
  }

  function onDragEnter(event: DragEvent) {
    if (current.disabled || !carriesFiles(event)) return;
    event.preventDefault();
    depth += 1;
    highlight(true);
  }

  function onDragOver(event: DragEvent) {
    if (current.disabled || !carriesFiles(event)) return;
    // Without this the browser navigates to the dropped file and the page is simply gone.
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  }

  function onDragLeave(event: DragEvent) {
    if (current.disabled || !carriesFiles(event)) return;
    depth -= 1;
    if (depth <= 0) highlight(false);
  }

  function onDrop(event: DragEvent) {
    if (current.disabled || !carriesFiles(event)) return;
    event.preventDefault();
    highlight(false);

    const input = resolveInput();
    const dropped = Array.from(event.dataTransfer?.files ?? []);
    if (!input || input.disabled || dropped.length === 0) return;

    const files = input.multiple ? dropped : dropped.slice(0, 1);
    const usable = files.filter((file) => acceptsFile(file, input.accept));
    if (usable.length === 0) {
      current.onerror?.("errors.upload_type");
      return;
    }

    const transfer = new DataTransfer();
    for (const file of usable) transfer.items.add(file);
    input.files = transfer.files;
    // The control's own `onchange` is what uploads; this is the whole point of landing on the
    // input rather than beside it.
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  node.addEventListener("dragenter", onDragEnter);
  node.addEventListener("dragover", onDragOver);
  node.addEventListener("dragleave", onDragLeave);
  node.addEventListener("drop", onDrop);

  return {
    update(next) {
      current = next ?? {};
      if (current.disabled) highlight(false);
    },
    destroy() {
      node.removeEventListener("dragenter", onDragEnter);
      node.removeEventListener("dragover", onDragOver);
      node.removeEventListener("dragleave", onDragLeave);
      node.removeEventListener("drop", onDrop);
    },
  };
};
