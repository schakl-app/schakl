import type { LayoutServerLoad } from "./$types";

// Tenant theme, locale and user are resolved once in hooks and shared with every page.
export const load: LayoutServerLoad = async ({ locals }) => ({
  theme: locals.theme,
  locale: locals.locale,
  user: locals.user,
});
