import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { interactionActions } from "$lib/modules/interactions/actions.server";
import { INTERACTION_COLUMNS, INTERACTIONS_TABLE_ID } from "$lib/modules/interactions/columns";

import type { Actions, PageServerLoad } from "./$types";

const PAGE_SIZE = 50;

/**
 * The Interacties page (#168): every interaction the viewer may see — team-visible logged
 * rows plus their own pending queue (#172) — searchable, in the shared `DataTable`. Holders
 * of `interactions.interaction.read_all` get an owner filter (everyone / a specific person);
 * everyone else is locked to "mijn" as the only narrowing, enforced by the API, not here.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "interactions.interaction.read")) throw redirect(303, "/");
  const canReadAll = can(event.locals.user, "interactions.interaction.read_all");

  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, INTERACTIONS_TABLE_ID);
  const resolved = resolveColumns(INTERACTION_COLUMNS, pref);

  const params = event.url.searchParams;
  const q = params.get("q")?.trim() || undefined;
  const kind = params.get("kind") || undefined;
  const pending = params.get("status") === "pending";
  const mine = params.get("mine") === "1";
  const owner = canReadAll ? params.get("owner") || undefined : undefined;
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const api = apiFor(event);
  const [list, kinds, members] = await Promise.all([
    api.GET("/api/v1/interactions", {
      params: {
        query: {
          limit: PAGE_SIZE,
          offset,
          q,
          kind,
          status: pending ? "pending" : undefined,
          mine: mine || undefined,
          owner_user_id: !mine ? owner : undefined,
        },
      },
    }),
    api.GET("/api/v1/interactions/kinds", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/members/lookup"),
  ]);

  return {
    items: list.data?.items ?? [],
    total: list.data?.total ?? 0,
    offset,
    limit: PAGE_SIZE,
    kinds: kinds.data ?? [],
    members: members.data ?? [],
    canReadAll,
    filters: { q: q ?? "", kind: kind ?? null, pending, mine, owner: owner ?? null },
    table: { pref, sort: null, widths: resolved.widths },
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Personal, in-view column layout (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, INTERACTIONS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },
  ...interactionActions,
};
