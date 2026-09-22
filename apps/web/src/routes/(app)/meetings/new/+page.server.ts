import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { createContactAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The recorder. No load of its own beyond the gate: the lookups come from the section layout,
 * and every write here is the browser talking to the API while it records — a form action
 * cannot carry a stream of pieces. The one action is the participants editor's inline
 * contact create (docs/UX.md: every entity-reference picker offers one).
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "meetings.meeting.write")) throw redirect(303, "/meetings");
  // Whether the consent statement is asked for, and how long the audio is kept: the org's
  // policy (Instellingen → Vergaderingen), read by whoever may record.
  const { data: policy } = await apiFor(event).GET("/api/v1/meetings/policy");
  return {
    // The client hub's ＋ chip lands here with the client already chosen.
    companyId: event.url.searchParams.get("company") ?? "",
    consentRequired: policy?.consent_required ?? true,
    retentionDays: policy?.audio_retention_days ?? 30,
  };
};

export const actions: Actions = {
  createContact: createContactAction,
};
