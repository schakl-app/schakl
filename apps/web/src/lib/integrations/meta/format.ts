/**
 * Labels, states and limits for the Meta screens — one place, read by the planner, the composer,
 * the settings screen and the agenda feed, so a status never has two words for it.
 */
import { hasMessage, t } from "$lib/core/i18n";
import type { UiState } from "$lib/core/state";

import type { Channel, MetaCredential, SocialPostIssue } from "./types";

/** The statuses a list opens on: everything still going on (CLAUDE.md §9, #329). */
export const WORKING = "working";

export const POST_STATUSES = [
  "draft",
  "review",
  "scheduled",
  "publishing",
  "published",
  "partial",
  "failed",
  "cancelled",
] as const;

export function statusLabel(status: string): string {
  return t(`meta.status.${status}`);
}

/**
 * The same meaning in the fixed state palette (docs/UX.md §1). A draft is `neutral` — it is not
 * a claim about anything — while a post waiting for approval is `today`: not a fault, but
 * nobody may scroll past it.
 */
export function statusState(status: string): UiState {
  switch (status) {
    case "failed":
      return "late";
    case "partial":
      return "late";
    case "review":
      return "today";
    case "scheduled":
    case "publishing":
    case "handed_over":
      return "soon";
    case "published":
      return "ok";
    default:
      return "neutral";
  }
}

export function targetStatusLabel(status: string): string {
  return t(`meta.target.${status}`);
}

/** Is a worker still holding the post — the states the screen polls through. */
export function inFlight(status: string): boolean {
  return status === "publishing";
}

export function channelLabel(channel: string): string {
  return channel === "instagram" ? "Instagram" : "Facebook";
}

export function kindLabel(kind: string): string {
  return t(`meta.kind.${kind}`);
}

/** What each channel takes. The API is the authority; these draw the counter beside the box. */
export const LIMITS: Record<Channel, { body: number; hashtags: number | null }> = {
  facebook: { body: 63_206, hashtags: null },
  instagram: { body: 2_200, hashtags: 30 },
};

export function countHashtags(text: string): number {
  return (text.match(/(?<![\p{L}\p{N}_])#[\p{L}\p{N}_]+/gu) ?? []).length;
}

/**
 * One issue as a sentence. The code is an i18n key and the details are its literals — the limit,
 * the count, which image — so the sentence names the number rather than alluding to it (#305).
 */
export function issueText(issue: SocialPostIssue): string {
  const details = Object.fromEntries(
    Object.entries(issue.details ?? {}).map(([key, value]) => [key, String(value)]),
  );
  return t(issue.code, details);
}

/**
 * A stored error as a sentence. Ours are i18n keys (`meta.error.*`); Meta's own are prose in
 * whatever language Meta chose, shown as they are — it is the one thing that says *what* to fix.
 */
export function errorText(value: string | null | undefined): string {
  if (!value) return "";
  if (value.startsWith("meta.") && hasMessage(value)) return t(value);
  return value;
}

/** What to *do* about a failure, from its envelope code — beside Meta's own sentence. */
export function adviceFor(code: string | null | undefined): string | null {
  if (!code) return null;
  const key = `meta.advice.${code}`;
  return hasMessage(key) ? t(key) : null;
}

export function capabilityLabel(name: string): string {
  return t(`meta.capability.${name}`);
}

/** The capabilities a token lacks, as the things it therefore cannot do. */
export function missingCapabilities(credential: MetaCredential): string[] {
  return Object.entries(credential.capabilities ?? {})
    .filter(([, held]) => !held)
    .map(([name]) => name);
}

/**
 * A token's clock as a state: fine, running out, or dead. `soon` starts where the nightly job
 * starts refreshing, so an amber token is one the system is already working on.
 */
export function credentialState(credential: MetaCredential): UiState {
  if (credential.status === "expired") return "late";
  if (credential.status === "error") return "late";
  if (credential.refresh_error) return "today";
  if (credential.days_left != null && credential.days_left <= 20) return "soon";
  return "ok";
}
