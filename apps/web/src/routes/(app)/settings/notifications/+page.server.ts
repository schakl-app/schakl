import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { EMPTY_MATRIX, parseMatrixPayload } from "$lib/modules/notifications/prefs.server";

import type { Actions, PageServerLoad } from "./$types";

// Personal delivery preferences — reachable by every member (NOT manager-gated, unlike the org
// defaults next door). Reached from the profile menu, because what reaches *me* is mine
// (docs/UX.md §6). External channels (#17) are admin-only and shown below the matrix.
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const canManageChannels = can(event.locals.user, "notifications.channels.manage");
  const [prefs, channels] = await Promise.all([
    api.GET("/api/v1/notifications/preferences"),
    canManageChannels
      ? api.GET("/api/v1/notifications/channels")
      : Promise.resolve({ data: null }),
  ]);
  return {
    matrix: prefs.data ?? EMPTY_MATRIX,
    canManageChannels,
    channels: channels.data ?? [],
  };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const body = parseMatrixPayload(form.get("payload"));
    if (!body) return fail(400, { error: "errors.validation" });

    const { error } = await apiFor(event).PUT("/api/v1/notifications/preferences", { body });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  /** Delete this user's rows: every event falls back to what the org (or the code) says. */
  reset: async (event) => {
    const { error } = await apiFor(event).PUT("/api/v1/notifications/preferences", {
      body: { events: [], general: null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  // --- external channels (#17), admin-only (the API re-enforces) ---------------------- #
  createChannel: async (event) => {
    const form = await event.request.formData();
    const kind = String(form.get("kind") ?? "").trim();
    const name = String(form.get("name") ?? "").trim();
    const url = String(form.get("url") ?? "").trim();
    if (!kind || !name || !url) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/notifications/channels", {
      body: { kind: kind as "slack", name, url, enabled: true, event_filter: [] },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { channelSaved: true };
  },

  testChannel: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("channel_id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST(
      "/api/v1/notifications/channels/{channel_id}/test",
      { params: { path: { channel_id: id } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // Surface the provider's real result — a broken webhook must be diagnosable.
    return { testOk: data?.ok ?? false, testError: data?.error ?? null };
  },

  deleteChannel: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("channel_id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/notifications/channels/{channel_id}", {
        params: { path: { channel_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { channelSaved: true };
  },
};
