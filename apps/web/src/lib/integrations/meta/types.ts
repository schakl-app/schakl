/**
 * The shapes the Meta screens read, taken from the generated client so a field the API renames
 * is a compile error here rather than an empty cell on a screen.
 */
import type { components } from "$lib/core/api/schema";

export type MetaSettings = components["schemas"]["MetaSettingsRead"];
export type MetaCredential = components["schemas"]["MetaCredentialRead"];
export type MetaAsset = components["schemas"]["MetaAssetRead"];
export type SocialPost = components["schemas"]["SocialPostRead"];
export type SocialPostTarget = components["schemas"]["SocialPostTargetRead"];
export type SocialPostIssue = components["schemas"]["SocialPostIssue"];
export type SocialPostMedia = components["schemas"]["SocialPostMediaRead"];
export type SocialPostCounts = components["schemas"]["SocialPostCounts"];
export type MetaPublishedPost = components["schemas"]["MetaPublishedPost"];

export type Channel = "facebook" | "instagram";
export type PostStatus = SocialPost["status"];

/** What the composer needs to know about a destination: enough to draw a chip and a preview. */
export interface ChannelOption {
  id: string;
  kind: "page" | "instagram";
  channel: Channel;
  name: string;
  username: string | null;
  pictureUrl: string | null;
  companyId: string | null;
  companyName: string | null;
  canPublish: boolean;
  blockedBy: string | null;
}

export function channelOf(kind: string): Channel {
  return kind === "instagram" ? "instagram" : "facebook";
}

export function toChannelOption(asset: MetaAsset): ChannelOption {
  return {
    id: asset.id,
    kind: asset.kind === "instagram" ? "instagram" : "page",
    channel: channelOf(asset.kind),
    name: asset.name,
    username: asset.username ?? null,
    pictureUrl: asset.picture_url ?? null,
    companyId: asset.company_id ?? null,
    companyName: asset.company_name ?? null,
    canPublish: asset.can_publish,
    blockedBy: asset.blocked_by ?? null,
  };
}
