import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { toChannelOption } from "$lib/integrations/meta/types";

import type { LayoutServerLoad } from "./$types";

/**
 * The URL-independent lookups under `marketing/social/`: the channels a post can go to, the
 * state of the connection, and the clients the planner is narrowed by. A layout load, so a
 * search keystroke, a status pill or a page step reruns none of them (docs/PERFORMANCE.md).
 *
 * Only the **linked** channels are fetched. What a token has found and nobody has linked yet
 * can be hundreds of rows at an agency, and the planner needs one number about them — which
 * the status read carries — not the rows.
 */
export const load: LayoutServerLoad = async (event) => {
  if (!can(event.locals.user, "meta.asset.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const [assets, status, companies] = await Promise.all([
    api.GET("/api/v1/meta-business/assets", {
      params: { query: { active_only: true, limit: 500, count: false } },
    }),
    api.GET("/api/v1/meta-business/status"),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, meta: false, sort: "name" } },
    }),
  ]);
  return {
    // What a post can be sent to: linked, switched on, and a channel rather than an ad account.
    channels: (assets.data?.items ?? [])
      .filter((asset) => asset.kind !== "ad_account")
      // A Page before its Instagram account, everywhere the two are drawn side by side.
      .toSorted((a, b) => Number(a.kind === "instagram") - Number(b.kind === "instagram"))
      .map(toChannelOption),
    status: status.data ?? {
      connected: false,
      writes_enabled: true,
      facebook_scheduler: "schakl" as const,
      channels_linked: 0,
      channels_unlinked: 0,
      ad_accounts_linked: 0,
      ad_accounts_unlinked: 0,
      needs_attention: false,
    },
    companies: (companies.data?.items ?? []).map((company) => ({
      id: company.id,
      name: company.name,
      status: company.status,
    })),
    canWrite: can(event.locals.user, "meta.post.write"),
    canPublish: can(event.locals.user, "meta.post.publish"),
    canManage: can(event.locals.user, "meta.settings.manage"),
  };
};
