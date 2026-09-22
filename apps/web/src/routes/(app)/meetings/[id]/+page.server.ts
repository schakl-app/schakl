import { error as httpError, fail, redirect } from "@sveltejs/kit";

import type { components } from "$lib/core/api/schema";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * One meeting: its state while the worker has it, the draft to review, the record once it is
 * confirmed. One API call; the screen re-reads *this* load while a worker holds the row.
 */
export const load: PageServerLoad = async (event) => {
  event.depends("meetings:meeting");
  const { data, error, response } = await apiFor(event).GET("/api/v1/meetings/{meeting_id}", {
    params: { path: { meeting_id: event.params.id } },
  });
  if (error || !data) throw httpError(response?.status ?? 404);
  return { meeting: data };
};

type MinutesBody = components["schemas"]["MinutesDraft"];

function minutesFrom(form: FormData): MinutesBody | null {
  const raw = String(form.get("minutes") ?? "");
  if (!raw) return null;
  try {
    return JSON.parse(raw) as MinutesBody;
  } catch {
    return null;
  }
}

export const actions: Actions = {
  /** The definition fields — title, client, project, kind. */
  update: async (event) => {
    const form = await event.request.formData();
    const text = (name: string) =>
      form.has(name) ? String(form.get(name) ?? "").trim() : undefined;
    const body: Record<string, unknown> = {};
    const title = text("title");
    if (title !== undefined && title) body.title = title;
    const kind = text("kind");
    if (kind) body.kind = kind;
    if (form.has("company_id")) body.company_id = text("company_id") || null;
    if (form.has("project_id")) body.project_id = text("project_id") || null;
    const { error } = await apiFor(event).PATCH("/api/v1/meetings/{meeting_id}", {
      params: { path: { meeting_id: event.params.id } },
      body,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },

  /** The reviewer's names for the speaker labels. */
  speakers: async (event) => {
    const form = await event.request.formData();
    const speakers: Record<string, string> = {};
    for (const [key, value] of form.entries()) {
      if (key.startsWith("speaker:") && String(value).trim()) {
        speakers[key.slice("speaker:".length)] = String(value).trim();
      }
    }
    const { error } = await apiFor(event).PUT("/api/v1/meetings/{meeting_id}/speakers", {
      params: { path: { meeting_id: event.params.id } },
      body: { speakers },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  /** The edited draft, kept without confirming. */
  saveMinutes: async (event) => {
    const minutes = minutesFrom(await event.request.formData());
    if (!minutes) return fail(400, { error: "errors.validation" });
    const { error } = await apiFor(event).PUT("/api/v1/meetings/{meeting_id}/minutes", {
      params: { path: { meeting_id: event.params.id } },
      body: minutes,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  /** The draft becomes a contact moment and tasks. */
  confirm: async (event) => {
    const form = await event.request.formData();
    const minutes = minutesFrom(form);
    if (!minutes) return fail(400, { error: "errors.validation" });
    const { data, error } = await apiFor(event).POST("/api/v1/meetings/{meeting_id}/confirm", {
      params: { path: { meeting_id: event.params.id } },
      body: { minutes },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { confirmed: true, skipped: data?.skipped ?? [] };
  },

  retry: async (event) => {
    const { error } = await apiFor(event).POST("/api/v1/meetings/{meeting_id}/retry", {
      params: { path: { meeting_id: event.params.id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { queued: true };
  },

  deleteAudio: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/meetings/{meeting_id}/audio", {
      params: { path: { meeting_id: event.params.id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  delete: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/meetings/{meeting_id}", {
      params: { path: { meeting_id: event.params.id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, "/meetings");
  },
};
