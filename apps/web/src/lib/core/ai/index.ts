/**
 * AI web core (epic #131): the one gate every AI affordance checks, and shared types.
 *
 * "Off means invisible" (#126): an affordance renders only when the tenant enabled the
 * feature (from `/meta/me`, riding the session payload — no extra call) AND the user holds
 * `ai.use`. Browser-safe: no session.ts import.
 */
import { can, type PermissionHolder } from "$lib/core/permissions";

/** The tenant-togglable features, plus `speech` — a *capability*, not a toggle: it is
 *  reported only when the org has a provider that can actually transcribe (#246). Anthropic
 *  has no speech endpoint and is the default, so without this an Anthropic org would be shown
 *  a microphone that 409s on first click. */
export type AIFeature =
  | "assistant"
  | "writing_assist"
  | "time_assist"
  | "reporting"
  | "speech";

/** Svelte context key the (app) layout provides; shared components (the editor's writing
 *  assist) read it so no consumer needs per-module wiring (#128). */
export const AI_CONTEXT_KEY = "schakl:ai";

export interface AIContext {
  enabled: (feature: AIFeature) => boolean;
}

/** The page entity the assistant inherits (#127). */
export interface AssistantEntity {
  entity_type: string;
  entity_id: string;
  label: string | null;
}

export interface AIUser extends PermissionHolder {
  aiFeatures?: string[];
}

/** May this user see the affordance for `feature`? UX gating only — the API is the boundary. */
export function aiEnabled(user: AIUser | null | undefined, feature: AIFeature): boolean {
  if (!user?.aiFeatures?.includes(feature)) return false;
  return can(user, "ai.use");
}

/** A record chip an AI answer cites (#127); the UI resolves it to a deep link. */
export interface AISource {
  type: string;
  id: string;
  label: string;
}

/** Where a source chip links to. Unknown types render as plain chips (no dead links). */
export function sourceHref(source: AISource): string | null {
  switch (source.type) {
    case "company":
      return `/companies/${source.id}`;
    case "contact":
      return `/contacts/${source.id}`;
    case "project":
      return `/projects/${source.id}`;
    case "task":
      return `/tasks/${source.id}`;
    case "time_report":
      return `/reports?company_id=${source.id}`;
    default:
      return null;
  }
}
