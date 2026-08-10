/**
 * The form actions behind the Cloudflare panel (epic #278). The domain detail page spreads
 * these into its `actions` — the same contract the Drive and interactions panels use: a panel
 * edits through its host page, because SvelteKit actions live on the page.
 *
 * Keeping them here rather than inline in `routes/(app)/domains/[id]/+page.server.ts` is what
 * stops the domains route from growing Cloudflare internals (CLAUDE.md §6): the host imports
 * one symbol and knows nothing about zones, rulesets or tokens.
 *
 * Every action is named `cf*` so it cannot collide with the host page's own.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

/** The four status codes a domain-wide redirect may use (see the API's REDIRECT_STATUS_CODES). */
const STATUS_CODES = [301, 302, 307, 308];

function statusCode(raw: FormDataEntryValue | null): 301 | 302 | 307 | 308 {
  const value = Number(raw ?? 301);
  return (STATUS_CODES.includes(value) ? value : 301) as 301 | 302 | 307 | 308;
}

/** The zone's records again, after a write. `null` if the re-read fails — the write still stood. */
async function reloadDns(event: RequestEvent, zone_id: string) {
  const { data } = await apiFor(event).GET("/api/v1/cloudflare/zones/{zone_id}/dns", {
    params: { path: { zone_id } },
  });
  return data ?? null;
}

export const cloudflareActions = {
  /** Adopt this domain's existing zone, or create one. Adoption always wins (see the API). */
  cfConnect: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST(
      "/api/v1/cloudflare/domains/{domain_id}/connect",
      {
        params: { path: { domain_id: event.params.id as string } },
        body: {
          account_id: String(form.get("account_id") ?? "") || null,
          create_if_missing: form.get("adopt_only") === null,
        },
      },
    );
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfConnected: true };
  },

  /**
   * The explicit "go look at Cloudflare" action. Its answer is returned to the page rather than
   * reloaded from `load`, because `load` deliberately reads only stored rows — a domain page
   * must not depend on Cloudflare being up (docs/PERFORMANCE.md).
   *
   * **Public DNS is refreshed first, and that is a different module's call.** Half of what this
   * button is asked — "do the nameservers point here yet?" — is not Cloudflare's to answer: the
   * observed side comes from the domains module's own resolver, which otherwise runs once a
   * night. So pressing this after changing nameservers at the registrar compared a freshly-read
   * Cloudflare against an observation up to a day old, and reported the delegation as wrong.
   *
   * The two calls are composed here rather than joined in the API, because `cloudflare` may not
   * reach into `domains` (CLAUDE.md §6) and runs no second resolver (docs/CLOUDFLARE.md §5).
   * The refresh is **best-effort on purpose**: it needs `domains.domain.write`, which a
   * Cloudflare-only admin may not hold, and a refusal there must not cost them the Cloudflare
   * check they *can* run. When it does not happen the report says so itself —
   * `nameservers_checked_at` carries the observation's age, and the verdict reads "unknown"
   * rather than "wrong".
   */
  cfCheck: async (event: RequestEvent) => {
    const api = apiFor(event);
    const domain_id = event.params.id as string;
    await api
      .POST("/api/v1/domains/{domain_id}/refresh", { params: { path: { domain_id } } })
      .catch(() => null);
    const { data, error } = await api.POST(
      "/api/v1/cloudflare/domains/{domain_id}/check",
      { params: { path: { domain_id } } },
    );
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfStatus: data };
  },

  cfSaveRedirect: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const target_url = String(form.get("target_url") ?? "").trim();
    if (!target_url) return fail(400, { cfError: "errors.required" });
    const { error } = await apiFor(event).PUT(
      "/api/v1/cloudflare/domains/{domain_id}/redirect",
      {
        params: { path: { domain_id: event.params.id as string } },
        body: {
          target_url,
          status_code: statusCode(form.get("status_code")),
          // Unchecked checkboxes are simply absent from the form data.
          preserve_path: form.get("preserve_path") !== null,
          preserve_query: form.get("preserve_query") !== null,
          include_subdomains: form.get("include_subdomains") !== null,
          ensure_origin: form.get("ensure_origin") !== null,
        },
      },
    );
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfRedirectSaved: true };
  },

  /**
   * Claim a Redirect Rule the zone already has, instead of appending a second one beside it.
   *
   * Carries the *same* intent fields as the save, because that is what the API compares the live
   * rule against: adoption succeeds only where Cloudflare already holds exactly the rule schakl
   * would have written. There is no `ensure_origin` — adopting writes nothing at Cloudflare, and
   * a checkbox implying otherwise would be the control lying about what it does.
   */
  cfAdoptRedirect: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const target_url = String(form.get("target_url") ?? "").trim();
    const rule_id = String(form.get("rule_id") ?? "");
    if (!target_url || !rule_id) return fail(400, { cfError: "errors.required" });
    const { error } = await apiFor(event).POST(
      "/api/v1/cloudflare/domains/{domain_id}/redirect/adopt",
      {
        params: { path: { domain_id: event.params.id as string } },
        body: {
          rule_id,
          target_url,
          status_code: statusCode(form.get("status_code")),
          preserve_path: form.get("preserve_path") !== null,
          preserve_query: form.get("preserve_query") !== null,
          include_subdomains: form.get("include_subdomains") !== null,
        },
      },
    );
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfRedirectAdopted: true };
  },

  cfRemoveRedirect: async (event: RequestEvent) => {
    const { error } = await apiFor(event).DELETE(
      "/api/v1/cloudflare/domains/{domain_id}/redirect",
      { params: { path: { domain_id: event.params.id as string } } },
    );
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfRedirectRemoved: true };
  },

  /**
   * Fetch the zone's DNS records on demand. Deliberately an action rather than part of `load`:
   * this is a live Cloudflare call, and putting it on the page load would make every visit to
   * a domain wait on an outside API.
   */
  cfLoadDns: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const zone_id = String(form.get("zone_id") ?? "");
    if (!zone_id) return fail(400, { cfError: "errors.required" });
    const { data, error } = await apiFor(event).GET("/api/v1/cloudflare/zones/{zone_id}/dns", {
      params: { path: { zone_id } },
    });
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfDns: data };
  },

  cfExportDns: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const zone_id = String(form.get("zone_id") ?? "");
    const format = form.get("format") === "csv" ? "csv" : "bind";
    if (!zone_id) return fail(400, { cfError: "errors.required" });
    const { data, error } = await apiFor(event).GET(
      "/api/v1/cloudflare/zones/{zone_id}/dns/export",
      { params: { path: { zone_id }, query: { format } } },
    );
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfExport: data };
  },

  cfSaveDnsRecord: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const zone_id = String(form.get("zone_id") ?? "");
    const record_id = String(form.get("record_id") ?? "");
    const priority = String(form.get("priority") ?? "").trim();
    const body = {
      type: String(form.get("type") ?? "A").toUpperCase(),
      name: String(form.get("name") ?? "").trim(),
      content: String(form.get("content") ?? "").trim(),
      ttl: Number(form.get("ttl") ?? 1) || 1,
      proxied: form.get("proxied") !== null,
      priority: priority === "" ? null : Number(priority),
      comment: String(form.get("comment") ?? "").trim() || null,
    };
    if (!zone_id || !body.name || !body.content) return fail(400, { cfError: "errors.required" });
    const api = apiFor(event);
    const { error } = record_id
      ? await api.PATCH("/api/v1/cloudflare/zones/{zone_id}/dns/{record_id}", {
          params: { path: { zone_id, record_id } },
          body,
        })
      : await api.POST("/api/v1/cloudflare/zones/{zone_id}/dns", {
          params: { path: { zone_id } },
          body,
        });
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    // Hand the refreshed list straight back: the panel's table is filled from the last action's
    // result, and re-reading it here is one call instead of asking the user to press "show" again.
    return { cfDnsSaved: true, cfDns: await reloadDns(event, zone_id) };
  },

  cfDeleteDnsRecord: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const zone_id = String(form.get("zone_id") ?? "");
    const record_id = String(form.get("record_id") ?? "");
    if (!zone_id || !record_id) return fail(400, { cfError: "errors.required" });
    const { error } = await apiFor(event).DELETE(
      "/api/v1/cloudflare/zones/{zone_id}/dns/{record_id}",
      { params: { path: { zone_id, record_id } } },
    );
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfDnsDeleted: true, cfDns: await reloadDns(event, zone_id) };
  },

  cfLinkPages: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const project_id = String(form.get("project_id") ?? "");
    if (!project_id) return fail(400, { cfError: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/cloudflare/domains/{domain_id}/pages", {
      params: { path: { domain_id: event.params.id as string } },
      body: {
        project_id,
        hostname: String(form.get("hostname") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfPagesLinked: true };
  },

  cfUnlinkPages: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const link_id = String(form.get("link_id") ?? "");
    if (!link_id) return fail(400, { cfError: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/cloudflare/pages/links/{link_id}", {
      params: { path: { link_id } },
    });
    if (error) return fail(400, { cfError: apiErrorKey(error).key });
    return { cfPagesUnlinked: true };
  },
};
