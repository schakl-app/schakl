import { redirect } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

// Auth guard for the whole app area — anonymous users go to the login screen.
//
// The user's saved table layouts are fetched here rather than per list: a layout load does not
// rerun on filter/sort/tab navigation, so the column prefs cost one call per section instead of
// one per click (docs/PERFORMANCE.md). They are a small blob, and every list needs them before it
// can decide which columns — and therefore which aggregates — to ask the API for.
//
// The bell's unread count rides in the same `Promise.all` so it costs no extra round-trip. It is
// only the *first paint's* value: a layout load does not rerun on navigation, so the bell polls
// its own endpoint to stay live. A tenant without the module never pays for the call.
export const load: LayoutServerLoad = async (event) => {
  if (!event.locals.user) throw redirect(303, "/login");
  const api = apiFor(event);

  const notificationsEnabled = event.locals.theme?.enabledModules?.includes("notifications");
  const [prefs, unread, navPrefs] = await Promise.all([
    api.GET("/api/v1/prefs"),
    notificationsEnabled ? api.GET("/api/v1/notifications/unread-count") : undefined,
    // The saved sidebar layout (#169): own row → org default → none, resolved by the API.
    api.GET("/api/v1/nav/prefs"),
  ]);

  return {
    user: event.locals.user,
    prefs: prefs.data?.prefs ?? {},
    unreadCount: unread?.data?.count ?? 0,
    navPref: navPrefs.data ?? { items: null, source: "none" },
  };
};
