import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { impexAction } from "$lib/core/impex/actions.server";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { TLD_PRICE_COLUMNS, TLD_PRICES_TABLE_ID } from "$lib/modules/domains/columns";

import type { Actions, PageServerLoad } from "./$types";

const PRICE_MODES = ["percent", "amount", "set"] as const;

/** The fields the price-increase preview and apply share (#231's shape). `null` = invalid. */
function priceIncreaseBody(form: FormData) {
  const mode = String(form.get("mode") ?? "");
  const value = String(form.get("value") ?? "").trim();
  const valid_from = String(form.get("valid_from") ?? "").trim();
  if (!PRICE_MODES.includes(mode as (typeof PRICE_MODES)[number])) return null;
  if (!value || Number.isNaN(Number(value)) || !valid_from) return null;
  const scope = String(form.get("scope") ?? "all");
  const [kind, tld] = scope.includes(":") ? scope.split(":", 2) : [scope, ""];
  if (kind !== "all" && !tld) return null;
  return {
    mode: mode as (typeof PRICE_MODES)[number],
    value,
    valid_from,
    tld: kind === "tld" ? tld : null,
  };
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "domains.tld_price.read")) throw redirect(303, "/domains");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, TLD_PRICES_TABLE_ID);
  const resolved = resolveColumns(TLD_PRICE_COLUMNS, pref);

  const groups = await api.GET("/api/v1/domains/tld-prices");

  return {
    groups: groups.data ?? [],
    table: { pref, sort: null, widths: resolved.widths },
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Import/export from this list's own toolbar (issue #77) — the shared wizard's three steps. */
  impex: (event) => impexAction(event, "domain_tld_price"),
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, TLD_PRICES_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** Set a TLD's price: a same-day row corrects in place, any other date appends history. */
  savePrice: async (event) => {
    const form = await event.request.formData();
    const tld = String(form.get("tld") ?? "").trim();
    const amount = String(form.get("amount") ?? "").trim();
    if (!tld || !amount || Number.isNaN(Number(amount))) {
      return fail(400, { priceError: "errors.required" });
    }
    const valid_from = String(form.get("valid_from") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/domains/tld-prices", {
      body: { tld, amount, valid_from: valid_from || null },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { priceError: e.key, fields: e.fields });
    }
    return { priceSaved: true };
  },

  /** Remove one history row — undo a scheduled increase or a slip of the keyboard. */
  deletePrice: async (event) => {
    const form = await event.request.formData();
    const price_id = String(form.get("id") ?? "");
    if (price_id) {
      await apiFor(event).DELETE("/api/v1/domains/tld-prices/{price_id}", {
        params: { path: { price_id } },
      });
    }
    return { priceDeleted: true };
  },

  /** Preview: every in-scope TLD with its would-be amount — nothing written. */
  previewPriceIncrease: async (event) => {
    const form = await event.request.formData();
    const body = priceIncreaseBody(form);
    if (!body) return fail(400, { priceError: "errors.required" });
    const { data, error } = await apiFor(event).POST(
      "/api/v1/domains/tld-prices/price-increase/preview",
      { body },
    );
    if (error || !data) return fail(400, { priceError: apiErrorKey(error).key });
    // The scope echoes back so the modal only renders a preview made for what it shows.
    return { pricePreview: data, priceScope: String(form.get("scope") ?? "all") };
  },

  /** Apply: one dated price-history row per TLD. */
  applyPriceIncrease: async (event) => {
    const form = await event.request.formData();
    const body = priceIncreaseBody(form);
    if (!body) return fail(400, { priceError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/domains/tld-prices/price-increase", {
      body,
    });
    if (error || !data) return fail(400, { priceError: apiErrorKey(error).key });
    return { priceApplied: data.items.length };
  },
};
