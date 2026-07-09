/**
 * The *resolved* light/dark colour scheme, live-updated. `<html data-theme>` is the source of
 * truth (set by the SSR stamp or the no-flash inline script in app.html, and by the
 * matchMedia listener in `routes/+layout.svelte` while a `system` preference is active) — this
 * just mirrors it into a rune so components that can't express themselves via CSS (charts,
 * which compute raw SVG fill/stroke hex in script) can react without a page reload.
 */
let resolved = $state<"light" | "dark">("light");

function readHtmlTheme(): "light" | "dark" {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

if (typeof document !== "undefined") {
  resolved = readHtmlTheme();
}

/** Call once the DOM `data-theme` attribute changes (hydration, live OS-scheme flip, toggle). */
export function syncResolvedTheme(): void {
  if (typeof document === "undefined") return;
  resolved = readHtmlTheme();
}

export const resolvedTheme = {
  get current() {
    return resolved;
  },
};
