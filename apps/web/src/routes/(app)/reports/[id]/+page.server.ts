import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * One report: read it, fix its prose, send it (issue #300).
 *
 * This is the screen that makes "review before send" real. Without it the only options are
 * trusting the model or not using the feature — which is what the workflow this replaces chose,
 * and it chose trusting.
 *
 * The load is one API call. The document itself is framed from `/preview`, which renders the
 * *same* artefact the PDF prints, so what the reviewer approves is what the client receives.
 */
export const load: PageServerLoad = async (event) => {
  // Named so the screen can re-read *this* load while a worker is still generating, without
  // `invalidateAll()` dragging the layout's API calls along every few seconds.
  event.depends("reporting:report");
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/reporting/reports/{report_id}",
    { params: { path: { report_id: event.params.id } } },
  );
  // 404 for anything this caller may not see — including an internal analysis they lack the
  // permission for. The API already answered that way; the screen must not soften it.
  if (error || !data) throw httpError(response?.status ?? 404);

  return {
    report: data,
    canWrite: can(event.locals.user, "reporting.report.write"),
    canSend: can(event.locals.user, "reporting.report.send"),
    // The key the *destination* declares, not the one this screen is about (§15, #310): the
    // client's reporting page redirects a caller who lacks it, so a link gated on anything else
    // is a control that bounces.
    canManageProfile: can(event.locals.user, "reporting.profile.manage"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /**
   * A hand edit. The API records which sections a person touched, so a later regenerate leaves
   * them alone — silently replacing the sentence somebody just fixed is how a review button
   * stops being used.
   */
  narrative: async (event) => {
    const form = await event.request.formData();
    const key = String(form.get("section_key") ?? "");
    const text = String(form.get("text") ?? "");
    if (!key) return fail(400, { error: "errors.validation" });
    const { error } = await apiFor(event).PUT("/api/v1/reporting/reports/{report_id}/narrative", {
      params: { path: { report_id: event.params.id } },
      body: { narrative: { [key]: text } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key, section: key });
    return { saved: true, section: key };
  },

  /** Rewrite one paragraph against that section's own data — never the whole document. */
  rewrite: async (event) => {
    const form = await event.request.formData();
    const key = String(form.get("section_key") ?? "");
    if (!key) return fail(400, { error: "errors.validation" });
    const { error } = await apiFor(event).POST("/api/v1/reporting/reports/{report_id}/rewrite", {
      params: { path: { report_id: event.params.id } },
      body: { section_key: key },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key, section: key });
    return { rewritten: true, section: key };
  },

  publish: async (event) => {
    const form = await event.request.formData();
    const published = String(form.get("published") ?? "true") === "true";
    const { error } = await apiFor(event).POST("/api/v1/reporting/reports/{report_id}/publish", {
      params: { path: { report_id: event.params.id }, query: { published } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { published };
  },

  /** The act that puts the document in a client's inbox — its own permission, deliberately. */
  send: async (event) => {
    const { error } = await apiFor(event).POST("/api/v1/reporting/reports/{report_id}/send", {
      params: { path: { report_id: event.params.id } },
      body: { publish: true },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { sent: true };
  },

  regenerate: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST("/api/v1/reporting/reports/generate", {
      body: {
        company_id: String(form.get("company_id") ?? ""),
        audience: String(form.get("audience") ?? "client") as "client" | "internal",
        refresh_data: true,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // The API's own answer, not an assumption: it declines to start a second run over one that
    // is already going, and a banner that claims otherwise is the screen telling a small lie.
    return { queued: data?.queued ?? false };
  },

  delete: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/reporting/reports/{report_id}", {
      params: { path: { report_id: event.params.id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, "/reports");
  },
};
