/**
 * Per-tenant runtime theming (CLAUDE.md §7, Golden Rule 4).
 *
 * Branding comes from the API's org settings; nothing is hardcoded. The server load applies it
 * via CSS custom properties on <html> at first render, and the dynamic manifest/favicon routes
 * reuse the same values.
 */

export interface OrgTheme {
  brandName: string;
  showBrandName: boolean;
  logoUrl: string | null;
  faviconUrl: string | null;
  primaryColor: string;
  accentColor: string;
  defaultLocale: string;
  enabledModules: string[];
}

// Neutral fallback used only before/without tenant settings — not a product brand.
export const DEFAULT_THEME: OrgTheme = {
  brandName: "",
  showBrandName: true,
  logoUrl: null,
  faviconUrl: null,
  primaryColor: "#4f46e5",
  accentColor: "#0ea5e9",
  defaultLocale: "nl",
  enabledModules: ["companies"],
};

// The API validates colours as hex on write; re-check here because the value is interpolated
// into an HTML attribute, and fall back rather than emit anything else.
const HEX_COLOR = /^#[0-9a-fA-F]{3,8}$/;

function safeColor(value: string, fallback: string): string {
  return HEX_COLOR.test(value) ? value : fallback;
}

/**
 * Inline style string stamped onto <html> so SSR paints the brand colours immediately.
 *
 * It must land on the root element, not a wrapper: `accent-color` is an inherited property
 * computed once at :root, so a variable overridden further down the tree never reaches the
 * native form controls (date/time pickers, checkboxes, radios, range).
 */
export function themeStyle(theme: OrgTheme): string {
  const primary = safeColor(theme.primaryColor, DEFAULT_THEME.primaryColor);
  const accent = safeColor(theme.accentColor, DEFAULT_THEME.accentColor);
  return `--brand-primary:${primary};--brand-accent:${accent};`;
}
