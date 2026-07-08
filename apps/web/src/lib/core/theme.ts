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

/** Inline style string applied to <html> so SSR paints the brand colours immediately. */
export function themeStyle(theme: OrgTheme): string {
  return `--brand-primary:${theme.primaryColor};--brand-accent:${theme.accentColor};`;
}
