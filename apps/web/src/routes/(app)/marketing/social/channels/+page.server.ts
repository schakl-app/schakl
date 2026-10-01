import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { readTablePref } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";

import type { Actions, PageServerLoad } from "./$types";

const CHANNELS_TABLE_ID = "meta_channels";
const FILTERS = ["q", "kind", "state"] as const;

/**
 * Which Page, Instagram account and ad account is which client's.
 *
 * It lives **here, where the work is**, and not under Instellingen (docs/UX.md: the
 * credential is configuration, saying whose Page this is is the job). Instellingen → Meta
 * holds the app and the tokens and points back at this screen.
 *
 * It opens on **what still has to be decided** — the channels a token found that nobody has
 * linked — when there are any, because that is the queue; with none waiting it opens on what
 * is linked. `?state=all` is everything. The URL is the view, and the list pages.
 */
export const load: PageServerLoad = async (event) => {
  event.depends("meta:channels");
  const api = apiFor(event);
  const { prefs, status } = await event.parent();
  const pref = readTablePref(prefs, CHANNELS_TABLE_ID);
  const paging = resolvePaging(event.url, pref);
  const filters = readFilters(event.url, [...FILTERS]);
  const waiting = status.channels_unlinked + (status.ad_accounts_unlinked ?? 0);
  const state = filters.state ?? (waiting > 0 ? "unlinked" : "linked");
  const kind =
    filters.kind === "page" || filters.kind === "instagram" || filters.kind === "ad_account"
      ? filters.kind
      : undefined;

  const assets = await api.GET("/api/v1/meta-business/assets", {
    params: {
      query: {
        active_only: state === "linked",
        unlinked_only: state === "unlinked",
        kind,
        q: filters.q,
        limit: paging.limit,
        offset: paging.offset,
      },
    },
  });
  return {
    assets: assets.data?.items ?? [],
    total: assets.data?.total ?? 0,
    loadError: assets.error ? apiErrorKey(assets.error, "errors.server").key : null,
    paging,
    table: { pref },
    locale: event.locals.locale,
    state,
    filters: { q: filters.q ?? "", kind: kind ?? "", state: filters.state ?? "" },
  };
};

export const actions: Actions = {
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, CHANNELS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** Say whose asset this is. Naming a client switches it on; "eigen" makes it the agency's. */
  link: async (event) => {
    if (!can(event.locals.user, "meta.settings.manage")) {
      return fail(403, { error: "errors.forbidden" });
    }
    const form = await event.request.formData();
    const asset_id = String(form.get("asset_id") ?? "");
    const company_id = String(form.get("company_id") ?? "").trim() || null;
    // The channel itself first; a Page's Instagram account (or the reverse) rides along
    // only where the dialog offered it and the box stayed ticked. One at a time and in
    // order: the first refusal is reported, and what was linked before it stays linked.
    const also = form.getAll("also").map(String).filter(Boolean).slice(0, 5);
    const api = apiFor(event);
    for (const id of [asset_id, ...also]) {
      const { error } = await api.PATCH("/api/v1/meta-business/assets/{asset_id}", {
        params: { path: { asset_id: id } },
        body: { company_id, active: true },
      });
      if (error) {
        return fail(400, { error: apiErrorKey(error, "errors.server").key, asset_id: id });
      }
    }
    return { linked: asset_id };
  },

  /** Stop working on it here. Nothing changes at Meta, and its client stays on the row. */
  unlink: async (event) => {
    if (!can(event.locals.user, "meta.settings.manage")) {
      return fail(403, { error: "errors.forbidden" });
    }
    const form = await event.request.formData();
    const asset_id = String(form.get("asset_id") ?? "");
    const { error } = await apiFor(event).PATCH("/api/v1/meta-business/assets/{asset_id}", {
      params: { path: { asset_id } },
      body: { active: false },
    });
    if (error) return fail(400, { error: apiErrorKey(error, "errors.server").key, asset_id });
    return { unlinked: asset_id };
  },

  // The client picker's inline-create (docs/UX.md: every entity-reference picker offers it).
  createCompany: createCompanyAction,
};
