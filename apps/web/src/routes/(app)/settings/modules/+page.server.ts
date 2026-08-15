import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.branding.write")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [instance, tenant] = await Promise.all([
    api.GET("/api/v1/meta/modules"),
    api.GET("/api/v1/meta/tenant"),
  ]);
  return {
    // What this installation ships vs. what this workspace has switched on.
    available: instance.data?.enabled_modules ?? [],
    enabled: tenant.data?.enabled_modules ?? [],
    // Licensing (issue #137): which modules need a license, and which are currently usable.
    licensed: instance.data?.licensed_modules ?? [],
    entitled: instance.data?.entitled_modules ?? [],
    // Modules vs integrations, and what an integration cannot run without (CLAUDE.md §6a). From
    // the API rather than the web registry so this screen and the enable gate that will judge its
    // save answer from the same authority — a screen that offers a combination the API refuses is
    // the "control that always refuses" #253 is about.
    kinds: instance.data?.module_kinds ?? {},
    requires: instance.data?.module_requires ?? {},
    // What "locked" means here, which differs per posture: a licence key the instance owner
    // installs, or a plan only the operator can change. Telling a cloud tenant to go and
    // install a key names a screen they cannot open and a fix that is not theirs (#253).
    deployment: tenant.data?.deployment ?? "self_hosted",
  };
};

export const actions: Actions = {
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
