/**
 * The AI Search overview's shapes (docs/SERANKING.md), read off the generated client rather than
 * written out a second time — a hand copy of a vocabulary the API owns is a second place to
 * remember, and `MarketingSource` already showed how that ends.
 */
import type { components } from "$lib/core/api/schema";

export type AiSearchOverview = components["schemas"]["AiSearchOverview"];
export type AiSearchEngineBlock = components["schemas"]["AiSearchEngineBlock"];
export type AiSearchMetric = components["schemas"]["AiSearchMetric"];
export type AiSearchSettingsRead = components["schemas"]["AiSearchSettingsRead"];
export type SeRankingCheck = components["schemas"]["SeRankingCheck"];
export type AiEngine = AiSearchEngineBlock["engine"];

/** Every engine choice, in display order. `all` is SE Ranking's own cross-engine aggregate. */
export const AI_ENGINES: AiEngine[] = [
  "all",
  "ai-overview",
  "ai-mode",
  "chatgpt",
  "perplexity",
  "gemini",
];
export const AI_SCOPES = ["base_domain", "domain", "url"] as const;

/** The monthly streams a block may carry, in the order the trend switcher offers them. */
export const AI_SERIES = [
  "link_presence",
  "average_position",
  "ai_traffic",
  "organic_traffic",
  "overall_traffic",
] as const;
export type AiSeries = (typeof AI_SERIES)[number];

/** What one read costs the agency, per engine choice — SE Ranking's own price. */
export const UNITS_PER_ENGINE = 800;
export const UNITS_BRAND_LOOKUP = 100;
