import { error, fail, redirect, type RequestEvent } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * One post: its words, its channels, its pictures, its time — and what came of it.
 *
 * Two reads, in parallel: the post, and its trail. The channels and clients are the section
 * layout's (`../+layout.server.ts`), so neither is fetched again here.
 */
export const load: PageServerLoad = async (event) => {
  event.depends("meta:post");
  const api = apiFor(event);
  const [post, trail] = await Promise.all([
    api.GET("/api/v1/meta-business/posts/{post_id}", {
      params: { path: { post_id: event.params.id } },
    }),
    can(event.locals.user, "activity.read")
      ? api.GET("/api/v1/activity", {
          params: { query: { entity_type: "meta_post", entity_id: event.params.id, limit: 30 } },
        })
      : Promise.resolve({ data: null }),
  ]);
  if (!post.data) throw error(404, "errors.not_found");
  return {
    post: post.data,
    // The feed's own shape: an absent name is "nobody", which it spells `null`.
    trail: (trail.data ?? []).map((entry) => ({
      ...entry,
      actor_name: entry.actor_name ?? null,
      impersonator_name: entry.impersonator_name ?? null,
    })),
  };
};

type Editable = {
  asset_ids?: string[];
  format?: "post" | "reel";
  body?: string;
  link?: string | null;
  notes?: string;
  scheduled_at?: string | null;
  media?: { kind: "image" | "video"; file_id?: string; url?: string; alt: string }[];
  overrides?: Record<string, string>;
};

/**
 * The composer's form, as the API's update body.
 *
 * **Every action that may be preceded by an edit reads the whole form**, so "Inplannen"
 * schedules what is on the screen rather than what was last saved: one gesture is one save
 * (docs/UX.md). The clock is sent **naked** — the API reads a naive time on the org's own
 * calendar (CLAUDE.md §8), so the browser never converts.
 */
function readForm(form: FormData): Editable {
  const body: Editable = {};
  if (form.has("body")) body.body = String(form.get("body") ?? "");
  if (form.has("link")) body.link = String(form.get("link") ?? "").trim() || null;
  if (form.has("notes")) body.notes = String(form.get("notes") ?? "");
  if (form.has("format")) body.format = form.get("format") === "reel" ? "reel" : "post";
  if (form.has("asset_ids_json")) {
    try {
      const parsed = JSON.parse(String(form.get("asset_ids_json") || "[]"));
      if (Array.isArray(parsed)) body.asset_ids = parsed.map(String).filter(Boolean);
    } catch {
      // A field the browser built and somebody broke: leave the channels alone.
    }
  }
  if (form.has("day")) {
    const day = String(form.get("day") ?? "").trim();
    const time = String(form.get("time") ?? "").trim() || "09:00";
    body.scheduled_at = day ? `${day}T${time}:00` : null;
  }
  if (form.has("media_json")) {
    try {
      const parsed = JSON.parse(String(form.get("media_json") || "[]"));
      if (Array.isArray(parsed)) {
        body.media = parsed
          .filter((item) => item && (item.file_id || item.url))
          .map((item) => ({
            kind: item.kind === "video" ? ("video" as const) : ("image" as const),
            ...(item.file_id ? { file_id: String(item.file_id) } : { url: String(item.url) }),
            alt: String(item.alt ?? ""),
          }));
      }
    } catch {
      // A field the browser built and somebody broke: leave the media alone rather than
      // replace it with nothing.
    }
  }
  if (form.has("overrides_json")) {
    try {
      const parsed = JSON.parse(String(form.get("overrides_json") || "{}"));
      if (parsed && typeof parsed === "object") {
        body.overrides = Object.fromEntries(
          Object.entries(parsed).map(([key, value]) => [key, String(value ?? "")]),
        );
      }
    } catch {
      // See above.
    }
  }
  return body;
}

interface Refusal {
  error: string;
  fields?: Record<string, string>;
}

function refusal(failure: unknown, fallback = "errors.server"): Refusal {
  const { key, fields } = apiErrorKey(failure, fallback);
  return { error: key, fields };
}

/** Save what is on the form, when the form carries anything to save. */
async function save(event: RequestEvent, form: FormData): Promise<Refusal | null> {
  if (!form.has("editable")) return null;
  const body = readForm(form);
  if (body.asset_ids && body.asset_ids.length === 0) {
    return { error: "meta.issue.no_channel", fields: { channels: "meta.issue.no_channel" } };
  }
  const { error: failure } = await apiFor(event).PATCH("/api/v1/meta-business/posts/{post_id}", {
    params: { path: { post_id: event.params.id ?? "" } },
    body,
  });
  return failure ? refusal(failure) : null;
}

type Verb = "offer" | "recall" | "schedule" | "publish" | "unschedule" | "cancel" | "retry";

const VERB_PATHS = {
  offer: "/api/v1/meta-business/posts/{post_id}/offer",
  recall: "/api/v1/meta-business/posts/{post_id}/recall",
  schedule: "/api/v1/meta-business/posts/{post_id}/schedule",
  publish: "/api/v1/meta-business/posts/{post_id}/publish",
  unschedule: "/api/v1/meta-business/posts/{post_id}/unschedule",
  cancel: "/api/v1/meta-business/posts/{post_id}/cancel",
  retry: "/api/v1/meta-business/posts/{post_id}/retry",
} as const;

/** Save, then move the post along. A refused save ends it: nothing is moved on stale words. */
function verb(name: Verb, outcome: string) {
  return async (event: RequestEvent) => {
    const form = await event.request.formData();
    const refused = await save(event, form);
    if (refused) return fail(400, { ...refused, verb: name });
    const api = apiFor(event);
    const params = { path: { post_id: event.params.id ?? "" } };
    const { error: failure } =
      name === "schedule"
        ? await api.POST(VERB_PATHS.schedule, { params, body: {} })
        : await api.POST(VERB_PATHS[name], { params });
    if (failure) return fail(400, { ...refusal(failure), verb: name });
    return { done: outcome };
  };
}

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const refused = await save(event, form);
    if (refused) return fail(400, { ...refused, verb: "save" });
    return { done: "saved" };
  },

  /**
   * Pictures. The words on the form are saved first — an upload reloads the post, and a
   * reload that lost the paragraph somebody had just typed would make attaching a picture
   * the riskiest thing on the page.
   */
  upload: async (event) => {
    const form = await event.request.formData();
    const uploads = form.getAll("file").filter((f): f is File => f instanceof File && f.size > 0);
    if (uploads.length === 0) return fail(400, { fileError: "errors.required" });
    const refused = await save(event, form);
    if (refused) return fail(400, { ...refused, verb: "upload" });

    const api = apiFor(event);
    const post_id = event.params.id ?? "";
    const current = await api.GET("/api/v1/meta-business/posts/{post_id}", {
      params: { path: { post_id } },
    });
    if (!current.data) return fail(404, { fileError: "errors.not_found" });
    const media: Editable["media"] = current.data.media.map((item) => ({
      kind: item.kind,
      ...(item.file_id ? { file_id: item.file_id } : { url: item.url ?? "" }),
      alt: item.alt ?? "",
    }));
    for (const upload of uploads) {
      const body = new FormData();
      body.append("file", upload, upload.name);
      const url = new URL(`${apiBaseUrl()}/api/v1/files`);
      url.searchParams.set("entity_type", "meta_post");
      url.searchParams.set("entity_id", post_id);
      const res = await event.fetch(url, {
        method: "POST",
        headers: {
          cookie: event.request.headers.get("cookie") ?? "",
          "x-forwarded-host": event.request.headers.get("host") ?? "",
        },
        body,
      });
      if (!res.ok) {
        return fail(400, {
          fileError: res.status === 413 ? "errors.upload_too_large" : "errors.upload_type",
        });
      }
      const stored = (await res.json()) as { id: string };
      media.push({ kind: "image", file_id: stored.id, alt: "" });
    }
    const { error: failure } = await api.PATCH("/api/v1/meta-business/posts/{post_id}", {
      params: { path: { post_id } },
      body: { media },
    });
    if (failure) {
      const { key, fields } = apiErrorKey(failure, "errors.server");
      return fail(400, { fileError: Object.values(fields ?? {})[0] ?? key });
    }
    return { done: "uploaded" };
  },

  offer: verb("offer", "offered"),
  recall: verb("recall", "recalled"),
  schedule: verb("schedule", "scheduled"),
  publish: verb("publish", "publishing"),
  unschedule: verb("unschedule", "unscheduled"),
  cancel: verb("cancel", "cancelled"),
  retry: verb("retry", "retrying"),

  duplicate: async (event) => {
    const { data, error: failure } = await apiFor(event).POST(
      "/api/v1/meta-business/posts/{post_id}/duplicate",
      { params: { path: { post_id: event.params.id ?? "" } } },
    );
    if (failure || !data) return fail(400, { ...refusal(failure), verb: "duplicate" });
    throw redirect(303, `/marketing/social/${data.id}`);
  },

  delete: async (event) => {
    const { error: failure } = await apiFor(event).DELETE("/api/v1/meta-business/posts/{post_id}", {
      params: { path: { post_id: event.params.id ?? "" } },
    });
    if (failure) return fail(400, { error: apiErrorKey(failure, "errors.server").key });
    throw redirect(303, "/marketing/social");
  },
};
