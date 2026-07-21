import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  SUBSCRIPTION_TYPE_COLUMNS,
  SUBSCRIPTION_TYPES_TABLE_ID,
} from "$lib/modules/subscriptions/columns";
import { manageActions } from "$lib/modules/subscriptions/manage.server";
import { subscriptionTypeLabel, type SubscriptionType } from "$lib/modules/subscriptions/types";

import type { Actions, PageServerLoad } from "./$types";

/** The catalog is small and fetched whole, so this load — not the API — honours `?sort=`. */
function sortTypes(
  items: SubscriptionType[],
  sort: string | undefined,
  locale: string,
): SubscriptionType[] {
  if (!sort) return items;
  const key = sort.replace(/^-/, "");
  const value = (st: SubscriptionType): string | number => {
    switch (key) {
      case "label":
        return subscriptionTypeLabel(st, locale).toLocaleLowerCase();
      case "key":
        return st.key;
      case "tasks":
        return (st.task_template_ids ?? []).length;
      case "active":
        return st.active ? 0 : 1;
      default:
        return 0;
    }
  };
  const sorted = items.toSorted((a, b) => {
    const va = value(a);
    const vb = value(b);
    return va < vb ? -1 : va > vb ? 1 : 0;
  });
  return sort.startsWith("-") ? sorted.toReversed() : sorted;
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "subscriptions.type.manage")) throw redirect(303, "/subscriptions");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, SUBSCRIPTION_TYPES_TABLE_ID);
  const resolved = resolveColumns(SUBSCRIPTION_TYPE_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const statusFilter = event.url.searchParams.get("status") || "";
  const q = event.url.searchParams.get("q") || "";

  const [types, taskTemplates] = await Promise.all([
    api.GET("/api/v1/subscriptions/types", { params: { query: { include_inactive: true } } }),
    // The dialog's spawn-on-activation picker; type.manage-gated page, so the read grant is there.
    api.GET("/api/v1/tasks/templates"),
  ]);

  const needle = q.trim().toLocaleLowerCase();
  const filtered = (types.data ?? []).filter(
    (st) =>
      (!statusFilter || (statusFilter === "active") === st.active) &&
      (!needle ||
        st.key.toLocaleLowerCase().includes(needle) ||
        subscriptionTypeLabel(st, event.locals.locale).toLocaleLowerCase().includes(needle)),
  );

  return {
    types: sortTypes(filtered, sort, event.locals.locale),
    taskTemplates: (taskTemplates.data ?? []).map((tpl) => ({ id: tpl.id, name: tpl.name })),
    statusFilter,
    q,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, SUBSCRIPTION_TYPES_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  // The shared catalog actions (manage.server.ts); this page uses saveType/toggleType/deleteType.
  ...manageActions,
};
