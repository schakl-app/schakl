/**
 * The server half both enablement screens share (issue #378) — the load and the save.
 *
 * Separate from `enablement.ts` because that one is imported by the *components*: a single shared
 * module holding both would pull `$lib/core/session` (and through it `$env/dynamic/private`) into
 * the browser bundle, which SvelteKit refuses at build time and, more to the point, would ship
 * server configuration to a page.
 */
import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "../errors";
import { can } from "../permissions";
import { apiFor } from "../session";

import type { EnablementData } from "./enablement";

import type { Actions, RequestEvent } from "@sveltejs/kit";

/** The load both enablement screens run; identical, because they edit one list from two angles. */
export async function enablementData(event: RequestEvent): Promise<EnablementData> {
  if (!can(event.locals.user, "settings.branding.write")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [instance, tenant] = await Promise.all([
    api.GET("/api/v1/meta/modules"),
    api.GET("/api/v1/meta/tenant"),
  ]);
  return {
    available: instance.data?.enabled_modules ?? [],
    enabled: tenant.data?.enabled_modules ?? [],
    // Licensing (issue #137): which modules need a licence, and which are currently usable.
    licensed: instance.data?.licensed_modules ?? [],
    entitled: instance.data?.entitled_modules ?? [],
    // Modules vs integrations, and what an integration cannot run without (CLAUDE.md §6a). From
    // the API rather than the web registry so these screens and the gate that will judge their
    // save answer from the same authority — a screen that offers a combination the API refuses is
    // the "control that always refuses" #253 is about.
    kinds: instance.data?.module_kinds ?? {},
    requires: instance.data?.module_requires ?? {},
    // What "locked" means here, which differs per posture: a licence key the instance owner
    // installs, or a plan only the operator can change. Telling a cloud tenant to go and install
    // a key names a screen they cannot open and a fix that is not theirs (#253).
    deployment: tenant.data?.deployment ?? "self_hosted",
  };
}

/** The save both enablement screens post; the form carries the whole set, not this screen's half. */
export const enablementActions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const enabled_modules = form.getAll("modules").map(String).filter(Boolean);
    const { error } = await apiFor(event).PATCH("/api/v1/meta/tenant", {
      body: { enabled_modules },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: true };
  },
};
