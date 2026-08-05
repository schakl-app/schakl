import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  asEntityType,
  NOTIFICATION_COLUMNS,
  NOTIFICATIONS_TABLE_ID,
} from "$lib/modules/notifications/columns";

import type { Actions, PageServerLoad } from "./$types";

/**
 * One call: the inbox page. The unread total the bell shows is the layout's, not this page's —
 * counting the loaded rows would count the page rather than the set (docs/UX.md, #37).
 */
export const load: PageServerLoad = async (event) => {
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, NOTIFICATIONS_TABLE_ID);
  const resolved = resolveColumns(NOTIFICATION_COLUMNS, pref);

  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const unreadOnly = event.url.searchParams.get("unread") === "1";
  const entityType = asEntityType(event.url.searchParams.get("entity_type"));
  const paging = resolvePaging(event.url, pref);

  const { data } = await apiFor(event).GET("/api/v1/notifications", {
    params: {
      query: {
        limit: paging.limit,
        offset: paging.offset,
        sort,
        unread: unreadOnly ? true : undefined,
        entity_type: entityType,
      },
    },
  });

  return {
    items: data?.items ?? [],
    total: data?.total ?? 0,
    paging,
    unreadOnly,
    entityType: entityType ?? null,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

export const actions: Actions = {
  /** Personal, in-view column layout (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, NOTIFICATIONS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** A reversible toggle, not a delete — read and unread are the same action. */
  markRead: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/notifications/{notification_id}", {
      params: { path: { notification_id: id } },
      body: { read: form.get("read") === "true" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { marked: true };
  },

  markAllRead: async (event) => {
    const { error } = await apiFor(event).POST("/api/v1/notifications/mark-all-read");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { markedAll: true };
  },
};
