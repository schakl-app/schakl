import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instellingen → Marketing (#134): the org's Google Ads developer token, stored encrypted per-org
// rather than as instance env config. Admin-only (marketing.link.manage); the token is write-only —
// the API reports `ads_developer_token_configured` and never plays the value back.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "marketing.link.manage")) throw redirect(303, "/settings");
  const { data } = await apiFor(event).GET("/api/v1/marketing/settings");
  return { settings: data ?? null };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    // Empty means "keep the stored token" — the API never returns it.
    const token = String(form.get("ads_developer_token") ?? "").trim() || null;
    // Same write-only rule for the SE Ranking key (#300): empty keeps what is stored.
    const seranking = String(form.get("seranking_api_key") ?? "").trim() || null;
    // The house comparison every client dashboard inherits (#312). Unlike the two secrets it is
    // a plain choice with no "keep what is stored" state to preserve, so an unrecognised value
    // is dropped rather than written.
    const raw = String(form.get("default_compare") ?? "");
    const default_compare = raw === "year" || raw === "previous" ? raw : null;
    // The house rule for keyword positions (#373). Same shape as the comparison above: a plain
    // choice, so an unrecognised source is dropped rather than written. The two checkboxes are
    // read by presence (`checked`), never against a literal — a control that posts "true" and a
    // check for "on" is the silent-false bug this codebase has already paid for once.
    const source = String(form.get("rankings_source") ?? "");
    const { error } = await apiFor(event).PUT("/api/v1/marketing/settings", {
      body: {
        ads_developer_token: token,
        seranking_api_key: seranking,
        default_compare,
        rankings: {
          source: (["auto", "seranking", "search_console", "off"].includes(source)
            ? source
            : null) as "auto" | "seranking" | "search_console" | "off" | null,
          limit: Number(form.get("rankings_limit")) || null,
          min_impressions:
            form.get("rankings_min_impressions") === ""
              ? null
              : Number(form.get("rankings_min_impressions")),
          max_position: Number(form.get("rankings_max_position")) || null,
          grouped: checked(form, "rankings_grouped"),
          show_landing_pages: checked(form, "rankings_show_landing_pages"),
        },
        // The house rule for a client with several websites (#381). Like the source above it,
        // an unrecognised value is dropped rather than written — this is a two-option select,
        // and a third value could only ever come from something that is not this form.
        report: {
          split: (["per_website", "combined"].includes(String(form.get("report_split")))
            ? String(form.get("report_split"))
            : null) as "per_website" | "combined" | null,
        },
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
};
