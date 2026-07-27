import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { EMPTY_MATRIX, parseMatrixPayload } from "$lib/modules/notifications/prefs.server";

import type { Actions, PageServerLoad } from "./$types";

// Personal delivery preferences — reachable by every member (NOT manager-gated, unlike the org
// defaults next door). Reached from the profile menu, because what reaches *me* is mine
// (docs/UX.md §6). E-mail is now per event, inside the matrix (#245), and so is each of my own
// external channels (#283); the org's *shared* channels stay admin-only, below.
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const canManageChannels = can(event.locals.user, "notifications.channels.manage");
  const canManageOwnChannels = can(event.locals.user, "notifications.channels.manage_own");
  const [prefs, channels] = await Promise.all([
    api.GET("/api/v1/notifications/preferences"),
    // One call for both sections: the API already scopes the list — an admin sees every
    // channel, a member only their own — so asking twice would just be a second round trip.
    canManageOwnChannels
      ? api.GET("/api/v1/notifications/channels")
      : Promise.resolve({ data: null }),
  ]);
  const all = channels.data ?? [];
  const me = event.locals.user?.id ?? "";
  return {
    matrix: prefs.data ?? EMPTY_MATRIX,
    canManageChannels,
    canManageOwnChannels,
    /** The org's shared rooms — admin-configured, channel-level cadence. */
    channels: all.filter((c) => c.user_id == null),
    /** My own transports — routed per event from the matrix above. */
    myChannels: all.filter((c) => c.user_id === me),
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

const CADENCES = new Set(["immediate", "hourly", "daily", "weekly"]);

/**
 * The channel's own delivery cadence (#283). The time and weekday controls only render for the
 * cadences that have one, so an absent field means "not applicable" and is posted as `null` —
 * that is also what clears a stale 08:00 off a channel switched back to immediate.
 */
function parseChannelCadence(form: FormData): {
  digest: string;
  digest_time: string | null;
  digest_weekday: number | null;
} {
  const digest = String(form.get("digest") ?? "immediate");
  return {
    digest: CADENCES.has(digest) ? digest : "immediate",
    ...parseChannelSchedule(form),
  };
}

/**
 * Only the *schedule* — when this channel's digests land (#283). A personal channel has no
 * channel-level cadence (the matrix sets one per event), but its daily and weekly digests still
 * need an hour, and asking for it once per channel beats asking on every matrix row.
 */
function parseChannelSchedule(form: FormData): {
  digest_time: string | null;
  digest_weekday: number | null;
} {
  const time = String(form.get("digest_time") ?? "");
  const weekday = Number(form.get("digest_weekday"));
  return {
    digest_time: /^\d{2}:\d{2}$/.test(time) ? time : null,
    digest_weekday: Number.isInteger(weekday) && weekday >= 0 && weekday <= 6 ? weekday : null,
  };
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

  // --- external channels (#17, #283) --------------------------------------------------- #
  // Two kinds of channel share these actions. A **shared** one belongs to the org and needs
  // `notifications.channels.manage`; a **personal** one is the caller's own and needs only
  // `manage_own`, which every member holds. The `personal` field is what the form says it
  // wants; the API decides what it may have (it forces `user_id` for a member either way).
  createChannel: async (event) => {
    const form = await event.request.formData();
    const kind = String(form.get("kind") ?? "").trim();
    const name = String(form.get("name") ?? "").trim();
    const personal = form.get("personal") != null;
    // Telegram is the one guided form with two inputs; the API expects "<token>/<chat id>".
    const url =
      kind === "telegram"
        ? `${String(form.get("bot_token") ?? "").trim()}/${String(form.get("chat_id") ?? "").trim()}`
        : String(form.get("url") ?? "").trim();
    if (!kind || !name || !url || url === "/")
      return fail(400, { channelError: "errors.required", channelErrorPersonal: personal });
    const { error } = await apiFor(event).POST("/api/v1/notifications/channels", {
      body: {
        kind: kind as "slack",
        name,
        url,
        enabled: true,
        // A personal channel is routed per event from the matrix, so it carries no filter and
        // no channel-level cadence — only the schedule its digests land on (#283). `digest`
        // still goes along at its default: the column is NOT NULL and simply unread for a
        // personal channel.
        event_filter: personal ? [] : parseChannelFilter(form.get("event_filter")),
        user_id: personal ? event.locals.user?.id : undefined,
        digest: personal ? "immediate" : parseChannelCadence(form).digest,
        ...parseChannelSchedule(form),
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, {
        channelError: e.fields?.url ?? e.key,
        channelErrorPersonal: personal,
      });
    }
    return { channelSaved: true };
  },

  /** Edit a channel's name, enabled state, and routing (#245, #283). The URL is never touched.
   *  Errors surface as `updateError` (distinct from the create form's `channelError`) so a failed
   *  edit reports next to the inline editor, not under the create form far below. */
  updateChannel: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("channel_id") ?? "");
    if (!id) return fail(400, { updateError: "errors.required", updateErrorId: id });
    const name = String(form.get("name") ?? "").trim();
    const personal = form.get("personal") != null;
    const { error } = await apiFor(event).PATCH("/api/v1/notifications/channels/{channel_id}", {
      params: { path: { channel_id: id } },
      body: {
        name: name || undefined,
        enabled: form.get("enabled") != null,
        // A personal channel's routing and cadence live in the matrix, so its editor only
        // sends the schedule; leaving the other fields off keeps them untouched (#283).
        event_filter: personal ? undefined : parseChannelFilter(form.get("event_filter")),
        digest: personal ? undefined : parseChannelCadence(form).digest,
        ...parseChannelSchedule(form),
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { updateError: e.fields?.url ?? e.key, updateErrorId: id });
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
