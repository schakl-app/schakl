import { redirect } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

// Auth guard for the whole app area — anonymous users go to the login screen.
//
// The user's saved table layouts are fetched here rather than per list: a layout load does not
// rerun on filter/sort/tab navigation, so the column prefs cost one call per section instead of
// one per click (docs/PERFORMANCE.md). They are a small blob, and every list needs them before it
// can decide which columns — and therefore which aggregates — to ask the API for.
export const load: LayoutServerLoad = async (event) => {
  if (!event.locals.user) throw redirect(303, "/login");

  const prefs = await apiFor(event).GET("/api/v1/prefs");
  return { user: event.locals.user, prefs: prefs.data?.prefs ?? {} };
};
