import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { EMPTY_MATRIX, parseMatrixPayload } from "$lib/modules/notifications/prefs.server";

import type { Actions, PageServerLoad } from "./$types";

// Personal delivery preferences — reachable by every member (NOT manager-gated, unlike the org
// defaults next door). Reached from the profile menu, because what reaches *me* is mine
// (docs/UX.md §6). E-mail is now per event, inside the matrix (#245); external channels (#17)
// are admin-only and shown below it.
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const canManageChannels = can(event.locals.user, "notifications.channels.manage");
  const [prefs, channels] = await Promise.all([
    api.GET("/api/v1/notifications/preferences"),
    canManageChannels ? api.GET("/api/v1/notifications/channels") : Promise.resolve({ data: null }),
  ]);
  return {
    matrix: prefs.data ?? EMPTY_MATRIX,
    canManageChannels,
    channels: channels.data ?? [],
  };
};

/** A hidden JSON field carries the channel's event filter; `[]` means "all events". */
function parseChannelFilter(raw: FormDataEntryValue | null): string[] {
  if (typeof raw !== "string") return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.filter((x): x is string => typeof x === "string");
  } catch {
    /* a malformed blob means "all events", never a 500 */
  }
  return [];
}

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
    // Telegram is the one guided form with two inputs; the API expects "<token>/<chat id>".
    const url =
      kind === "telegram"
        ? `${String(form.get("bot_token") ?? "").trim()}/${String(form.get("chat_id") ?? "").trim()}`
        : String(form.get("url") ?? "").trim();
    if (!kind || !name || !url || url === "/")
      return fail(400, { channelError: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/notifications/channels", {
      body: {
        kind: kind as "slack",
        name,
        url,
        enabled: true,
        // #245: which event types route here; [] = all events.
        event_filter: parseChannelFilter(form.get("event_filter")),
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { channelError: e.fields?.url ?? e.key });
    }
    return { channelSaved: true };
  },

  /** Edit a channel's name, enabled state, and event filter (#245). The URL is never touched. */
  updateChannel: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("channel_id") ?? "");
    if (!id) return fail(400, { channelError: "errors.required" });
    const name = String(form.get("name") ?? "").trim();
    const { error } = await apiFor(event).PATCH("/api/v1/notifications/channels/{channel_id}", {
      params: { path: { channel_id: id } },
      body: {
        name: name || undefined,
        enabled: form.get("enabled") != null,
        event_filter: parseChannelFilter(form.get("event_filter")),
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { channelError: e.fields?.url ?? e.key });
    }
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
