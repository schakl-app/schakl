import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
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
  // You land on **your own** moments (#263). The timeline stays team-visible — this is the
  // initial filter, not a permission change — so "iedereen" is one click away for everyone,
  // while naming a *colleague* is still the `read_all` grant (#168) and still enforced by the
  // API. `owner=all` is what says "everyone" out loud; no owner param at all means me, so the
  // older `?mine=1` links (the pending notification's, the widget's) land where they always did.
  const ownerParam = params.get("owner");
  const everyone = ownerParam === "all";
  const owner =
    canReadAll && ownerParam && ownerParam !== "all" && ownerParam !== "me"
      ? ownerParam
      : undefined;
  const mine = !everyone && !owner;
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  // Date navigation (#238): `from`/`to` are org-local calendar days; the week switcher and
  // month filter are just fast ways of writing this one range into the URL.
  const isoDay = (value: string | null) =>
    /^\d{4}-\d{2}-\d{2}$/.test(value ?? "") ? value! : undefined;
  const from = isoDay(params.get("from"));
  const to = isoDay(params.get("to"));
  // The URL wins over the saved default so a sorted list stays shareable (docs/UX.md).
  const sort = params.get("sort") ?? resolved.sort ?? undefined;

  const api = apiFor(event);
  const [list, kinds, members, companyDefinitions] = await Promise.all([
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
          date_from: from,
          date_to: to,
          sort,
        },
      },
    }),
    api.GET("/api/v1/interactions/kinds", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/members/lookup"),
    // For the inline company quick-create (#115): the full dialog includes custom fields.
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
  ]);

  return {
    items: list.data?.items ?? [],
    total: list.data?.total ?? 0,
    offset,
    limit: PAGE_SIZE,
    kinds: kinds.data ?? [],
    members: members.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
    canReadAll,
    filters: {
      q: q ?? "",
      kind: kind ?? null,
      pending,
      mine,
      everyone,
      owner: owner ?? null,
      /** What the owner `<select>` shows: "me" (the default), "all", or a colleague's id. */
      ownerValue: everyone ? "all" : (owner ?? "me"),
      from: from ?? null,
      to: to ?? null,
    },
    table: { pref, sort: sort ?? null, widths: resolved.widths },
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

  /** Inline company create behind the form's client picker (#115, docs/UX.md). */
  createCompany: createCompanyAction,

  /** Inline project create behind the form's project picker (docs/UX.md — per-picker
   *  definition of done). Answers `inlineCreated` so the picker that asked auto-selects it;
   *  `name`/`company_id` ride along so the form can label the option and run its cascade. */
  createProject: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { qcError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        status: "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        billable_default: true,
        custom: {},
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return {
      inlineCreated: {
        slot: "interaction_project",
        id: data.id,
        name: data.name,
        company_id: data.company_id ?? null,
      },
    };
  },

  ...interactionActions,
};
