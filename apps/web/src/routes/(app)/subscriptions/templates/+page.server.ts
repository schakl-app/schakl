import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  SUBSCRIPTION_TEMPLATE_COLUMNS,
  SUBSCRIPTION_TEMPLATES_TABLE_ID,
} from "$lib/modules/subscriptions/columns";
import { manageActions } from "$lib/modules/subscriptions/manage.server";
import {
  subscriptionTypeLabel,
  type SubscriptionTemplate,
  type SubscriptionType,
} from "$lib/modules/subscriptions/types";

import type { Actions, PageServerLoad } from "./$types";

const INTERVAL_ORDER: Record<string, number> = { monthly: 1, quarterly: 2, yearly: 3 };

/** The catalog is small and fetched whole, so this load — not the API — honours `?sort=`. */
function sortTemplates(
  items: SubscriptionTemplate[],
  sort: string | undefined,
  types: SubscriptionType[],
  locale: string,
): SubscriptionTemplate[] {
  if (!sort) return items;
  const key = sort.replace(/^-/, "");
  const value = (tpl: SubscriptionTemplate): string | number => {
    switch (key) {
      case "name":
        return tpl.name.toLocaleLowerCase();
      case "type":
        return subscriptionTypeLabel(
          types.find((st) => st.id === tpl.subscription_type_id),
          locale,
        ).toLocaleLowerCase();
      case "interval":
        return INTERVAL_ORDER[tpl.interval] ?? 0;
      case "amount":
        return tpl.amount == null ? -1 : Number(tpl.amount);
      case "included_hours":
        return tpl.included_hours == null ? -1 : Number(tpl.included_hours);
      case "notice_period_days":
        return tpl.notice_period_days ?? -1;
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
  if (!can(event.locals.user, "subscriptions.template.manage"))
    throw redirect(303, "/subscriptions");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, SUBSCRIPTION_TEMPLATES_TABLE_ID);
  const resolved = resolveColumns(SUBSCRIPTION_TEMPLATE_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const typeFilter = event.url.searchParams.get("type") || "";
  const q = event.url.searchParams.get("q") || "";

  const [templates, types] = await Promise.all([
    api.GET("/api/v1/subscriptions/templates"),
    // Inactive types included: an existing preset may still reference one, and its label
    // must not silently disappear from the table.
    api.GET("/api/v1/subscriptions/types", { params: { query: { include_inactive: true } } }),
  ]);

  const allTypes = types.data ?? [];
  const needle = q.trim().toLocaleLowerCase();
  const filtered = (templates.data ?? []).filter(
    (tpl) =>
      (!typeFilter || tpl.subscription_type_id === typeFilter) &&
      (!needle || tpl.name.toLocaleLowerCase().includes(needle)),
  );

  return {
    templates: sortTemplates(filtered, sort, allTypes, event.locals.locale),
    types: allTypes,
    typeFilter,
    q,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, SUBSCRIPTION_TEMPLATES_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  // The shared catalog actions (manage.server.ts); this page uses saveTemplate/deleteTemplate.
  ...manageActions,
};
