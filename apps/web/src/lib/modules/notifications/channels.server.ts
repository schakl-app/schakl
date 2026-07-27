/**
 * The connect / edit / test / remove actions behind `ChannelSection.svelte` (#17, #283, #295).
 *
 * One implementation, two mounts, because the forms are now identical: since #295 *which* events
 * reach a channel and *how often* is a column of the matrix on the same page, so a channel's own
 * form asks nothing but its name, its URL and the hour its digests land on — whether it is my
 * Slack DM or the team's `#crm` room.
 *
 * The only thing the two mounts disagree about is who owns the result, and that is the `scope`
 * argument: `"user"` stamps the caller's id onto a create, `"org"` leaves it null for a shared
 * room. The API decides what the caller may actually have (a member's create is forced onto their
 * own id regardless), so this is the form saying what it wants, never a permission check.
 *
 * `.server.ts`: never bundled to the browser.
 */
import { fail, type Actions, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

export type ChannelScope = "user" | "org";

/**
 * A channel's digest schedule — the hour and weekday its bundles land on. The controls always
 * render, so an absent or malformed field is a hostile post, not "not applicable": fall back to
 * `null`, which is what clears a stale 08:00 as well.
 */
function parseSchedule(form: FormData): {
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

export function channelActions(scope: ChannelScope): Actions {
  return {
    createChannel: async (event: RequestEvent) => {
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
          // A freshly connected channel is silent: nothing is routed to it until someone sets a
          // cell in the matrix above. Connecting a transport must not start pinging on its own.
          user_id: scope === "user" ? event.locals.user?.id : undefined,
          ...parseSchedule(form),
        },
      });
      if (error) {
        const e = apiErrorKey(error);
        return fail(400, { channelError: e.fields?.url ?? e.key });
      }
      return { channelSaved: true };
    },

    /** Edit a channel's name, enabled state and digest schedule. The URL is never touched: it is
     *  write-only, so "edit" cannot show it and rotating it means connecting the channel again.
     *  Errors surface as `updateError` (distinct from the create form's `channelError`) so a
     *  failed edit reports next to the inline editor, not under the create form far below. */
    updateChannel: async (event: RequestEvent) => {
      const form = await event.request.formData();
      const id = String(form.get("channel_id") ?? "");
      if (!id) return fail(400, { updateError: "errors.required", updateErrorId: id });
      const name = String(form.get("name") ?? "").trim();
      const { error } = await apiFor(event).PATCH("/api/v1/notifications/channels/{channel_id}", {
        params: { path: { channel_id: id } },
        body: {
          name: name || undefined,
          enabled: form.get("enabled") != null,
          ...parseSchedule(form),
        },
      });
      if (error) {
        const e = apiErrorKey(error);
        return fail(400, { updateError: e.fields?.url ?? e.key, updateErrorId: id });
      }
      return { channelSaved: true };
    },

    testChannel: async (event: RequestEvent) => {
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

    deleteChannel: async (event: RequestEvent) => {
      const form = await event.request.formData();
      const id = String(form.get("channel_id") ?? "");
      if (id) {
        const { error } = await apiFor(event).DELETE(
          "/api/v1/notifications/channels/{channel_id}",
          { params: { path: { channel_id: id } } },
        );
        if (error) return fail(400, { error: apiErrorKey(error).key });
      }
      return { channelSaved: true };
    },
  };
}
