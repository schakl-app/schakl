import { redirect } from "@sveltejs/kit";

import type { ApiClient } from "$lib/core/api/client";
import { bulkDeleteAction } from "$lib/core/bulk/actions.server";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { gmailActions } from "$lib/integrations/google/gmail-actions.server";
import { interactionActions } from "$lib/modules/interactions/actions.server";
import { INTERACTION_COLUMNS, INTERACTIONS_TABLE_ID } from "$lib/modules/interactions/columns";
import { RECORD_FIELDS, type RecordField } from "$lib/modules/interactions/scope";

import type { Actions, PageServerLoad } from "./$types";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The name of the record the list is scoped to (#323), for the chip that says so.
 *
 * One by-id read, and only when the URL actually names a record — a `null` here is a record the
 * caller may not read or that no longer exists, which the chip renders as an unresolved name
 * rather than dropping: a narrowed list that presents as everything is the whole bug.
 */
async function recordLabel(api: ApiClient, field: RecordField, id: string): Promise<string | null> {
  switch (field) {
    case "company_id": {
      const { data } = await api.GET("/api/v1/companies/{company_id}", {
        params: { path: { company_id: id } },
      });
      return data?.name ?? null;
    }
    case "project_id": {
      const { data } = await api.GET("/api/v1/projects/{project_id}", {
        params: { path: { project_id: id } },
      });
      return data?.name ?? null;
    }
    case "contact_id": {
      const { data } = await api.GET("/api/v1/contacts/{contact_id}", {
        params: { path: { contact_id: id } },
      });
      return data ? [data.first_name, data.last_name].filter(Boolean).join(" ") || null : null;
    }
    case "task_id": {
      const { data } = await api.GET("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
      });
      return data?.title ?? null;
    }
  }
}

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
  // What record this list is scoped to (#323) — the destination a panel's "8 van 137" links
  // to. All four have been API filters since #147; only this page never asked, so a hand-typed
  // `?company_id=` was silently ignored and listed everything. Several at once is legal (the
  // API ANDs them) and each gets its own chip; a non-uuid is dropped rather than 422'd, because
  // these arrive from a query string anyone can edit and an old bookmark can carry.
  const scoped = RECORD_FIELDS.flatMap((field) => {
    const id = params.get(field);
    return id && UUID_RE.test(id) ? [{ field, id }] : [];
  });
  const byField = (field: RecordField) => scoped.find((r) => r.field === field)?.id;
  // The one roll-up this list takes (#147): a project's own moments plus its tasks'. The panel
  // link carries it so the page counts what the panel counted.
  const include = params.get("include") === "tasks" ? "tasks" : undefined;
  // You land on **your own** moments (#263). The timeline stays team-visible — this is the
  // initial filter, not a permission change — so "iedereen" is one click away for everyone,
  // while naming a *colleague* is still the `read_all` grant (#168) and still enforced by the
  // API. `owner=all` is what says "everyone" out loud; no owner param at all means me, so the
  // older `?mine=1` links (the pending notification's, the widget's) land where they always did.
  //
  // Except when the URL names a record: that default answers an unfiltered firehose, and a
  // record view is already narrow. The panel it comes from is team-visible, so keeping "mijn"
  // here would land a link that said 137 on a list of 12 — #323's own bug, one screen to the
  // right. The owner select still narrows it, and it writes `owner=me` out loud so it can.
  const ownerParam = params.get("owner") || (scoped.length > 0 ? "all" : "me");
  const everyone = ownerParam === "all";
  const owner = canReadAll && ownerParam !== "all" && ownerParam !== "me" ? ownerParam : undefined;
  const mine = !everyone && !owner;
  const paging = resolvePaging(event.url, pref);
  // Date navigation (#238): `from`/`to` are org-local calendar days; the week switcher and
  // month filter are just fast ways of writing this one range into the URL.
  const isoDay = (value: string | null) =>
    /^\d{4}-\d{2}-\d{2}$/.test(value ?? "") ? value! : undefined;
  const from = isoDay(params.get("from"));
  const to = isoDay(params.get("to"));
  // The URL wins over the saved default so a sorted list stays shareable (docs/UX.md).
  const sort = params.get("sort") ?? resolved.sort ?? undefined;
  // `?interaction=<id>` opens that moment's detail modal on arrival (#184).
  const deepLinkId = params.get("interaction");
  const deepLink = deepLinkId && UUID_RE.test(deepLinkId) ? deepLinkId : null;

  // Only the URL-dependent read. The kind vocabulary, the member names and the company custom
  // fields come from the section layout, which does not rerun on a search keystroke, a date
  // click, an owner switch or a page step (#290).
  const api = apiFor(event);
  const [list, labels] = await Promise.all([
    api.GET("/api/v1/interactions", {
      params: {
        query: {
          limit: paging.limit,
          offset: paging.offset,
          q,
          kind,
          status: pending ? "pending" : undefined,
          mine: mine || undefined,
          owner_user_id: !mine ? owner : undefined,
          company_id: byField("company_id"),
          project_id: byField("project_id"),
          contact_id: byField("contact_id"),
          task_id: byField("task_id"),
          include,
          date_from: from,
          date_to: to,
          sort,
        },
      },
    }),
    // Nothing extra to pay for on the unscoped page: `scoped` is empty and this resolves at
    // once. Beside the list read, not after it, so naming the record costs no round trip.
    Promise.all(scoped.map((r) => recordLabel(api, r.field, r.id))),
  ]);

  /**
   * The deep-linked moment, when the page it landed on does not hold it.
   *
   * It used to be resolved *from the loaded rows*, which is true of the dashboard tile the
   * param was built for (#15 — always the newest few) and false of every other caller: a
   * notification about an @mention names a note somebody else wrote, weeks back, behind an
   * owner filter, so the link opened the list and said nothing. That is the worst answer
   * available to "open this one" — it looks exactly like a page that simply loaded.
   *
   * One by-id read, and only when the page came back without it, so the common case still costs
   * nothing. `null` is a moment that was deleted or that this caller may not read: the list is
   * then what they get, which is the same thing the old code did for every other reason.
   */
  const items = list.data?.items ?? [];
  const deepLinked =
    deepLink && !items.some((item) => item.id === deepLink)
      ? ((
          await api.GET("/api/v1/interactions/{interaction_id}", {
            params: { path: { interaction_id: deepLink } },
          })
        ).data ?? null)
      : null;

  return {
    items,
    deepLinked,
    total: list.data?.total ?? 0,
    paging,
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
      /** The records this list is narrowed to, named — one clearable chip each (#323). */
      records: scoped.map((r, i) => ({ ...r, label: labels[i] })),
      include: include ?? null,
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

  /**
   * Bulk delete, the one generic action a contact moment takes (there is nothing on one worth
   * setting across a selection, so no `bulkUpdate`). The three review actions are the module's
   * own and live in `interactionActions` below.
   */
  bulkDelete: (event) => bulkDeleteAction(event, "interaction"),

  // The page's own `createCompany` / `createProject` are gone: both now ride in
  // `interactionActions` as `createInteractionCompany` / `createInteractionProject`, so every
  // host that spreads them has the ＋, not just this page. The project one here also wrote a
  // name-and-client stub with no billable flag and none of the tenant's project custom fields,
  // which docs/UX.md rules out.
  ...interactionActions,

  /**
   * "Scan my mailbox now" (#341). The button is google's, not this list's — but this is the
   * screen where a missing email is noticed, so this is the page that hosts it, the same way
   * the detail pages host `driveActions`.
   */
  ...gmailActions,
};
