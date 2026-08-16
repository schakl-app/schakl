import { error, redirect } from "@sveltejs/kit";
import type { Actions } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  availabilityActions,
  resolveAvailabilityWindow,
} from "$lib/modules/leave/availability.server";
import { LEAVE_AVAILABILITY_TABLE_ID } from "$lib/modules/leave/columns";

import type { PageServerLoad } from "./$types";

/**
 * Beschikbaarheid — every availability row in a window, as rows you can open (#368).
 *
 * Before this, availability existed only as a section on one person's own page and as two ⋯
 * modals: answering "who can I book on the 14th" meant opening one modal per freelancer, and
 * looking at *last* month was impossible from any of them. `GET /leave/availability` already
 * answered both questions and only the calendar feed ever asked.
 *
 * **The window is the URL** (§9). `?from=`/`?to=` is what makes a span linkable and the past
 * reachable at all; `?user=` narrows to one person for a viewer who may read anybody's.
 *
 * **Its own permission decides who gets here** — `leave.availability.read`, never leave
 * approval. That is the same mistake #368 records one layer down: the roster gated its ⋯ item on
 * being an approver, so the permission the module invented could not be exercised by anyone who
 * held only it.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "leave.availability.read")) error(403, "errors.forbidden");
  const anyUser = can(event.locals.user, "leave.availability.read", "any");
  const writeAny = can(event.locals.user, "leave.availability.write", "any");
  const window = resolveAvailabilityWindow(event.url);
  // A `?user=` a viewer may not read is dropped rather than sent: the API would 403 the whole
  // page for a query parameter anybody can type into the address bar.
  const filterUser = (anyUser && event.url.searchParams.get("user")) || "";
  const { prefs, employmentType } = await event.parent();
  // **A permission decides who may write; the kind decides whether the surface exists** (§14).
  // Every member holds `leave.availability.write:own`, so the permission alone would make this a
  // page every employee can open about a thing employees do not have. The tab is already hidden
  // for them; this is the same rule at the URL, which is where a hidden tab is not a gate.
  // `employmentType === null` is *no period on file* and shows it to nobody, not to everybody.
  //
  // A redirect, not a 403: it is their own leave page they belong on, and nothing here was
  // forbidden — it simply is not about them. The layout already loaded the profile, so this
  // costs no call (docs/PERFORMANCE.md).
  if (!anyUser && employmentType !== "freelance") redirect(303, "/leave");

  const api = apiFor(event);
  const [rows, members] = await Promise.all([
    api.GET("/api/v1/leave/availability", {
      params: {
        query: {
          ...window,
          ...(filterUser
            ? { user_id: filterUser }
            : anyUser
              ? { all_users: true }
              : { user_id: event.locals.user?.id }),
        },
      },
    }),
    // The person filter and the ＋ picker; a viewer who may only keep their own week needs
    // neither, and pays for neither (docs/PERFORMANCE.md).
    anyUser ? api.GET("/api/v1/members/lookup") : Promise.resolve({ data: null }),
  ]);

  return {
    rows: rows.data ?? [],
    window,
    filterUser,
    anyUser,
    writeAny,
    members: members.data ?? [],
    pref: readTablePref(prefs, LEAVE_AVAILABILITY_TABLE_ID),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  // The same four writes every other availability host declares, so this page and the
  // freelancer's own section can never disagree about what a save does.
  ...availabilityActions,
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, LEAVE_AVAILABILITY_TABLE_ID, parseTablePref(form));
    return { ok: true };
  },
};
