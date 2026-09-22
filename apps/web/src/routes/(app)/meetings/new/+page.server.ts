import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";

import type { PageServerLoad } from "./$types";

/**
 * The recorder. No load of its own beyond the gate: the lookups come from the section layout,
 * and every write here is the browser talking to the API while it records — a form action
 * cannot carry a stream of pieces.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "meetings.meeting.write")) throw redirect(303, "/meetings");
  return {
    // The client hub's ＋ chip lands here with the client already chosen.
    companyId: event.url.searchParams.get("company") ?? "",
  };
};
