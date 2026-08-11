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
  const api = apiFor(event);
  const [instances, profiles, drifted] = await Promise.all([
    api.GET("/api/v1/uptime/instances"),
    // Profiles read on `monitor.read`, a different key from this screen's gate, so a role
    // holding only `instance.manage` must not fire a call that can do nothing but 403 (#310).
    can(event.locals.user, "uptime.monitor.read")
      ? api.GET("/api/v1/uptime/profiles")
      : Promise.resolve({ data: [] as never[] }),
    // The drift queue: monitors somebody changed in Uptime Kuma. Bounded, because this is a
    // section of a settings page and not a list screen — a tenant with two hundred drifted
    // monitors has a bigger problem than pagination (docs/PERFORMANCE.md).
    can(event.locals.user, "uptime.monitor.read")
      ? api.GET("/api/v1/uptime/monitors", {
          params: { query: { sync_status: "drift", limit: 50, offset: 0, count: true } },
        })
      : Promise.resolve({ data: null }),
  ]);
  return {
    instances: instances.data ?? [],
    profiles: profiles.data ?? [],
    drifted: drifted.data?.items ?? [],
    driftTotal: drifted.data?.total ?? 0,
  };
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

/**
 * The numeric fields a profile may set, read as *absent means inherit*.
 *
 * An empty box is not a zero. Sending `0` for a blank interval would pin every monitor
 * following this profile to the invariant floor, which is the kind of silent, plausible wrong
 * number nobody notices until a client asks why their site is checked every twenty seconds.
 */
function numericDefaults(form: FormData): Record<string, number> {
  const out: Record<string, number> = {};
  for (const key of ["interval_seconds", "retries"]) {
    const raw = String(form.get(key) ?? "").trim();
    if (raw === "") continue;
    const value = Number(raw);
    if (Number.isFinite(value)) out[key] = value;
  }
  return out;
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

  reconcile: async (event) => {
    const form = await event.request.formData();
    const direction = String(form.get("direction") ?? "");
    if (direction !== "push" && direction !== "adopt") {
      // No default direction, here as well as in the API: one overwrites a colleague's edit in
      // Uptime Kuma, the other overwrites schakl's record.
      return fail(400, { error: "errors.uptime_failed" });
    }
    const { error } = await apiFor(event).POST(
      "/api/v1/uptime/monitors/{monitor_id}/reconcile",
      {
        params: { path: { monitor_id: String(form.get("id") ?? "") } },
        body: { direction },
      },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { reconciled: direction };
  },

  createProfile: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/uptime/profiles", {
      body: {
        name: String(form.get("name") ?? "").trim(),
        monitor_type: String(form.get("monitor_type") ?? "http"),
        defaults: numericDefaults(form),
        is_default: checked(form, "is_default"),
        active: true,
        position: 0,
        notification_ids: [],
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { profileCreated: true };
  },

  deleteProfile: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).DELETE("/api/v1/uptime/profiles/{profile_id}", {
      params: { path: { profile_id: String(form.get("id") ?? "") } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { profileRemoved: true };
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
