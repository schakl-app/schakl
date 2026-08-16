import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, type ApiError } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

const FEATURES = [
  "assistant",
  "writing_assist",
  "time_assist",
  "task_assist",
  "reporting",
  "email_assist",
] as const;

// Instellingen → AI (#126): provider, write-only key, model, per-feature toggles, house
// style, budget and this month's meter. Admin-only (the API enforces `ai.settings.manage`).
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "ai.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [settings, usage] = await Promise.all([
    api.GET("/api/v1/ai/settings"),
    api.GET("/api/v1/ai/usage"),
  ]);
  return { ai: settings.data ?? null, usage: usage.data ?? null };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const text = (name: string) => String(form.get(name) ?? "").trim() || null;
    const provider = text("provider") as "anthropic" | "openai" | "openai_compatible" | null;
    if (!provider)
      return fail(400, { error: "errors.required", fields: undefined as ApiError["fields"] });
    const budgetRaw = text("monthly_token_budget");
    const budget = budgetRaw ? Number.parseInt(budgetRaw, 10) : null;

    const features: Record<string, { enabled: boolean; model: string | null }> = {};
    for (const feature of FEATURES) {
      features[feature] = {
        enabled: form.get(`feature_${feature}`) !== null,
        model: text(`model_${feature}`),
      };
    }

    // Speech is a separate credential on purpose (#246): Anthropic has no transcription
    // endpoint, so "reuse the chat provider" leaves the default tenant unable to dictate.
    // Empty here means "off", which clears the stored speech key with it.
    const speechProvider = text("speech_provider") as "openai" | "openai_compatible" | null;
    const audioBudgetRaw = text("monthly_audio_seconds_budget");
    const audioBudget = audioBudgetRaw ? Number.parseInt(audioBudgetRaw, 10) : null;

    const { error } = await apiFor(event).PUT("/api/v1/ai/settings", {
      body: {
        provider,
        // Empty means "keep the stored key" — the API never returns it.
        api_key: text("api_key"),
        base_url: text("base_url"),
        default_model: text("default_model"),
        features,
        house_style: text("house_style"),
        monthly_token_budget: budget && Number.isFinite(budget) ? budget : null,
        speech_provider: speechProvider,
        speech_api_key: text("speech_api_key"),
        speech_base_url: text("speech_base_url"),
        speech_model: text("speech_model"),
        monthly_audio_seconds_budget:
          audioBudget && Number.isFinite(audioBudget) ? audioBudget : null,
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },

  test: async (event) => {
    const { data, error } = await apiFor(event).POST("/api/v1/ai/settings/test");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { test: data };
  },

  remove: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/ai/settings");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { removed: true };
  },
};
