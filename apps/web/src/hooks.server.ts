/**
 * Server hooks (CLAUDE.md §7, §8).
 *
 * 1. Paraglide middleware binds the request locale (AsyncLocalStorage) so `m.*()` render in the
 *    right language during SSR, and stamps <html lang>.
 * 2. Context hook resolves the tenant theme + current user via the API and puts them on
 *    `event.locals` for layouts, guards, and the manifest/theme routes.
 */
import { sequence } from "@sveltejs/kit/hooks";
import type { Handle } from "@sveltejs/kit";

import "$lib/core/paraglide-strategy.server"; // register custom locale strategy (server)
import "$lib/modules"; // self-register web modules

import { fetchTenant, fetchUser } from "$lib/core/session";
import { getLocale } from "$lib/paraglide/runtime";
import { paraglideMiddleware } from "$lib/paraglide/server";

const handleParaglide: Handle = ({ event, resolve }) =>
  paraglideMiddleware(event.request, ({ request, locale }) => {
    event.request = request;
    return resolve(event, {
      transformPageChunk: ({ html }) => html.replace("%lang%", locale),
    });
  });

const handleContext: Handle = async ({ event, resolve }) => {
  event.locals.locale = getLocale();
  const [theme, user] = await Promise.all([fetchTenant(event), fetchUser(event)]);
  event.locals.theme = theme;
  event.locals.user = user;
  return resolve(event);
};

export const handle = sequence(handleParaglide, handleContext);
