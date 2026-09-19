import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { AI_ENGINES, AI_SCOPES, type AiEngine } from "$lib/modules/marketing/aisearch/types";
import { LABELLED_CONNECTIONS, PORTAL_LABEL_SOURCES } from "$lib/modules/marketing/types";

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
    // …and for the optional separate Data API key (docs/SERANKING.md §2). Removing one is its
    // own tick, because an empty box means "keep it" like every other secret on this form.
    const seranking_data = String(form.get("seranking_data_api_key") ?? "").trim() || null;
    // The house AI Search defaults. Engines are read by presence; none ticked is sent as
    // `null` (keep what is stored) rather than as an empty list, because "on, and asking
    // nothing" is not a state anybody means — switching it off is what the checkbox is for.
    const engines = form
      .getAll("ai_search_engines")
      .map(String)
      .filter((value): value is AiEngine => (AI_ENGINES as string[]).includes(value));
    const aiScope = String(form.get("ai_search_scope") ?? "");
    const aiSource = String(form.get("ai_search_source") ?? "")
      .trim()
      .toLowerCase();
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
    // Every source the form draws is posted (#446): an empty value clears that source back to
    // the default on the API, and a source this form does not draw is left as stored.
    const portal_source_labels = Object.fromEntries(
      [...PORTAL_LABEL_SOURCES, ...LABELLED_CONNECTIONS].map((key) => [
        key,
        String(form.get(`portal_label_${key}`) ?? "").trim(),
      ]),
    );
    // The house channel grouping for the leads dashboard (docs/MARKETING.md): one textarea per
    // group, a channel name per line. Posted whole — a grouping is four short lists.
    const channel_groups = Object.fromEntries(
      ["organic", "ads", "ai"].map((group) => [
        group,
        String(form.get(`channel_group_${group}`) ?? "")
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      ]),
    );
    const { error } = await apiFor(event).PUT("/api/v1/marketing/settings", {
      body: {
        channel_groups,
        ads_developer_token: token,
        seranking_api_key: seranking,
        seranking_data_api_key: seranking_data,
        clear_seranking_data_api_key: checked(form, "clear_seranking_data_api_key"),
        ai_search: {
          enabled: checked(form, "ai_search_enabled"),
          engines: engines.length ? engines : null,
          source: /^[a-z]{2}$/.test(aiSource) ? aiSource : null,
          scope: (AI_SCOPES as readonly string[]).includes(aiScope)
            ? (aiScope as (typeof AI_SCOPES)[number])
            : null,
        },
        default_compare,
        portal_source_labels,
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
  // Which of SE Ranking's two APIs the key reaches, and what the Data API plan has left
  // (docs/SERANKING.md §2). A key typed a moment ago is **saved first**: somebody who pastes a
  // key and presses "controleer" means that key, and checking the old one would answer a
  // question they did not ask. Nothing else on the form is written by this button.
  checkSeranking: async (event) => {
    const form = await event.request.formData();
    const api = apiFor(event);
    const seranking_api_key = String(form.get("seranking_api_key") ?? "").trim() || null;
    const seranking_data_api_key = String(form.get("seranking_data_api_key") ?? "").trim() || null;
    if (seranking_api_key || seranking_data_api_key) {
      const { error } = await api.PUT("/api/v1/marketing/settings", {
        // Only the two keys: every other field left out keeps what is stored.
        body: { seranking_api_key, seranking_data_api_key, clear_seranking_data_api_key: false },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    const { data, error } = await api.GET("/api/v1/marketing/settings/seranking/check");
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    return { check: data };
  },
};
