import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Uptime (docs/UPTIME.md): the Uptime Kuma connections and what they mirror.
 *
 * Org-wide configuration, so it lives here rather than as a button on a website (docs/UX.md
 * principle 6). Guarded whole on `uptime.instance.manage`: every control on this screen writes
 * or reveals credential state, so the gate belongs on the screen rather than on each button.
 *
 * The credential is write-only in both directions. The API reports `token_configured` and never
 * plays a value back; the password typed into the enrolment form is posted once, exchanged for
 * a token, and never stored — Uptime Kuma has no service accounts, so the account an agency
 * enrols is that instance's administrator and not storing its password is the only reduction in
 * blast radius available.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "uptime.instance.manage")) throw redirect(303, "/settings");
  const { data } = await apiFor(event).GET("/api/v1/uptime/instances");
  return { instances: data ?? [] };
};

/** Header pairs typed as `Name: value` lines — where a Cloudflare Access service token goes. */
function parseHeaders(raw: string): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const line of raw.split(/\r?\n/)) {
    const at = line.indexOf(":");
    if (at <= 0) continue;
    const name = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim();
    if (name && value) headers[name] = value;
  }
  return headers;
}

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const mode = String(form.get("mode") ?? "managed");
    const { data, error } = await apiFor(event).POST("/api/v1/uptime/instances", {
      body: {
        name: String(form.get("name") ?? "").trim(),
        mode: mode === "linked" ? "linked" : "managed",
        base_url: String(form.get("base_url") ?? "").trim() || null,
        // `checked()`, never `=== "on"`: a checkbox posts its *value* and an unticked one posts
        // nothing, so any read that names a particular value is a bug waiting for somebody to
        // change the control. Reporting shipped that mistake and every checkbox posted false.
        ssl_verify: !checked(form, "allow_insecure"),
        active: true,
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    return { created: true, id: data.id };
  },

  enrol: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const headersRaw = String(form.get("connect_headers") ?? "").trim();
    const { data, error } = await apiFor(event).POST(
      "/api/v1/uptime/instances/{instance_id}/enrol",
      {
        params: { path: { instance_id: id } },
        body: {
          username: String(form.get("username") ?? "").trim(),
          password: String(form.get("password") ?? ""),
          totp: String(form.get("totp") ?? "").trim() || null,
          // Absent leaves them alone; an explicit empty object clears them (§18's rule). Only
          // send the field when the admin actually typed in the box.
          connect_headers: headersRaw ? parseHeaders(headersRaw) : null,
        },
      },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // A refusal comes back as `ok: false` with a 200, on purpose: the report *is* the answer,
    // and raising would roll back the status update that makes the failure visible here.
    return { enrolled: true, result: data ?? null };
  },

  probe: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST(
      "/api/v1/uptime/instances/{instance_id}/probe",
      { params: { path: { instance_id: String(form.get("id") ?? "") } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { probed: true, result: data ?? null };
  },

  sync: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST(
      "/api/v1/uptime/instances/{instance_id}/sync",
      { params: { path: { instance_id: String(form.get("id") ?? "") } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { synced: true, report: data ?? null };
  },

  remove: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).DELETE("/api/v1/uptime/instances/{instance_id}", {
      params: { path: { instance_id: String(form.get("id") ?? "") } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { removed: true };
  },
};
