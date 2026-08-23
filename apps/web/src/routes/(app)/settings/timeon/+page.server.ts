import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import type { TimeonAccount, TimeonVerify } from "$lib/integrations/timeon/types";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Timeon: the credential, and the policy that decides what tonight's sync does.
 *
 * Org-wide configuration, so it lives here rather than as a button on the timesheet (docs/UX.md
 * principle 6). Three things about the shape are decisions rather than habit.
 *
 * **Two permissions, and the load mirrors the split rather than restating it.** Holding the
 * credential (`timeon.settings.manage`) and running a sync through it (`timeon.sync.run`) are
 * different grants — the operator who settles a conflict queue is not necessarily the person who
 * may rotate an API key. A sync-only caller reads the connections through `/accounts/options`,
 * which declares the weaker key and hands back the same shape, and never sees a credential
 * control. Asking `/accounts` for them would spend a request to render a 403 as an empty screen.
 *
 * **This load never calls Timeon.** Everything it reads answers from stored rows, so the screen
 * opens at full speed and still renders when Timeon is down — which is exactly when somebody
 * comes here to find out why. Verifying is the explicit *go and look* action, and it is a button.
 *
 * **The policy is one form.** Direction, window, protections and conflict rule are eight controls
 * whose *combination* is the thing that matters, so they save together and the screen states what
 * that combination will do (`SyncPlan.svelte`) before Save rather than after the first run.
 */
export const load: PageServerLoad = async (event) => {
  const mayManage = can(event.locals.user, "timeon.settings.manage");
  const maySync = can(event.locals.user, "timeon.sync.run");
  if (!mayManage && !maySync) throw redirect(303, "/settings");

  const typed = apiFor(event);
  const accountsRes = mayManage
    ? await typed.GET("/api/v1/timeon/accounts")
    : await typed.GET("/api/v1/timeon/accounts/options");
  const accounts: TimeonAccount[] = accountsRes.data ?? [];

  // The last few runs, so the screen can say when this connection last did anything without
  // a second visit. Ten, not twenty: the workspace is where a run log is *read*.
  const runs = maySync
    ? ((await typed.GET("/api/v1/timeon/runs", { params: { query: { limit: 5 } } })).data ?? [])
    : [];

  return { accounts, runs, mayManage, maySync };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const api_key = String(form.get("api_key") ?? "").trim();
    const { data, error } = await apiFor(event).POST("/api/v1/timeon/accounts", {
      body: {
        name: String(form.get("name") ?? "").trim(),
        api_key: api_key || null,
        base_url: String(form.get("base_url") ?? "").trim() || null,
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    // The API deliberately does not verify on create — a typo must not read as a failed save —
    // so the screen that *can* report it does so here. And a key that merely works is not the
    // answer anyway: which organisation it opens is, and only a verify knows that.
    const verified = api_key
      ? await apiFor(event).POST("/api/v1/timeon/accounts/{account_id}/verify", {
          params: { path: { account_id: data.id } },
        })
      : null;
    // `saved` and `verify` are independent facts and the page renders both: a rejected key is
    // still a stored key, and reporting only the refusal would let an admin believe the save
    // failed too and type it all in again.
    return {
      saved: true,
      createdId: data.id,
      verify: (verified?.data ?? null) as TimeonVerify | null,
    };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    // An empty key means "keep the stored one": the API never plays it back, so there is nothing
    // to send unchanged, and it reads a blank as absent rather than as "clear it" — a connection
    // with no key is disconnected, and removing it is what that is for.
    const api_key = String(form.get("api_key") ?? "").trim() || null;
    const floor = String(form.get("history_floor") ?? "").trim();
    const { error } = await apiFor(event).PATCH("/api/v1/timeon/accounts/{account_id}", {
      params: { path: { account_id } },
      body: {
        name: String(form.get("name") ?? "").trim() || null,
        api_key,
        active: checked(form, "active"),
        hours_direction: String(form.get("hours_direction") ?? "off") as never,
        projects_direction: String(form.get("projects_direction") ?? "off") as never,
        conflict_policy: String(form.get("conflict_policy") ?? "manual") as never,
        window_days: Number(form.get("window_days") ?? 45) || 45,
        // Always sent, `null` included: an empty date field is how "no floor" is expressed, and
        // omitting it would make clearing an existing floor impossible (§18's rule — absent
        // means leave alone, explicit null means clear).
        history_floor: floor || null,
        // Presence, never a particular posted value (`core/forms.checked`, #305). Comparing
        // against `"on"` while drawing a `FormCheckbox` — which posts `"true"` — is how every
        // checkbox in the reporting module silently posted `false` for a month.
        protect_invoiced: checked(form, "protect_invoiced"),
        protect_approved: checked(form, "protect_approved"),
        push_approvals: checked(form, "push_approvals"),
        create_missing_projects: checked(form, "create_missing_projects"),
        create_missing_users: checked(form, "create_missing_users"),
        auto_sync: checked(form, "auto_sync"),
        // The schedule (#388). Sent whatever `auto_sync` says, so switching automatic syncing
        // off and back on does not lose the cadence somebody chose — the switch is the on/off
        // and these three are *when*, which is a different question.
        auto_frequency: String(form.get("auto_frequency") ?? "daily") as never,
        auto_interval_hours: Number(form.get("auto_interval_hours") ?? 4) || 4,
        // "HH:MM" from `TimeInput`, which owns the control precisely so a machine set to en-US
        // does not ask a Dutch tenant for an AM/PM value (#13). A blank field is not a state
        // here — the column is NOT NULL and the API ignores a null — so the default stands in.
        auto_time: String(form.get("auto_time") ?? "").trim() || "04:20",
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  verify: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST(
      "/api/v1/timeon/accounts/{account_id}/verify",
      { params: { path: { account_id } } },
    );
    // A refused key is a `200` carrying `ok: false` (the probe succeeded; its answer was no), so
    // an `error` here means the request itself failed — an unreadable secret, a missing account.
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { verify: (data ?? null) as TimeonVerify | null };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/timeon/accounts/{account_id}", {
      params: { path: { account_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },
};
