/**
 * Cloud instance helpers (epic #199). Business-licensed — see this directory's LICENSE.
 *
 * The instance console lives on the apex host, where no org resolves — so the tenant-bound
 * `/meta/me` never answers there. These helpers speak to the posture endpoint
 * (`/meta/instance`) and the console identity endpoint (`/instance/me`) instead.
 *
 * **Server-only.** These call `apiFor`, so importing this module from a `.svelte` component
 * pulls the session helpers into the client bundle and breaks the production build at the
 * service-worker step (svelte-check does not catch it). Components take what they need from
 * `data` instead.
 */
import { apiFor, type ApiEvent } from "$lib/core/session";

export interface InstanceMeta {
  deployment: string;
  isInstanceHost: boolean;
  needsSetup: boolean;
  baseDomain: string;
}

export interface InstanceMe {
  id: string;
  email: string;
  fullName: string | null;
  /** Reaches the console at all: an owner, or a delegated admin holding something (#26). */
  isInstanceAdmin: boolean;
  /** The owner principal — the only one who may manage instance access. */
  isInstanceOwner: boolean;
  /** What this caller holds, owner expanded. UX only; the API is the boundary. */
  capabilities: string[];
}

export async function fetchInstanceMeta(event: ApiEvent): Promise<InstanceMeta | null> {
  const { data } = await apiFor(event).GET("/api/v1/meta/instance");
  if (!data) return null;
  return {
    deployment: data.deployment,
    isInstanceHost: data.is_instance_host,
    needsSetup: data.needs_setup,
    baseDomain: data.base_domain,
  };
}

export async function fetchInstanceMe(event: ApiEvent): Promise<InstanceMe | null> {
  const { data } = await apiFor(event).GET("/api/v1/instance/me");
  if (!data) return null;
  return {
    id: data.id,
    email: data.email,
    fullName: data.full_name ?? null,
    isInstanceAdmin: data.is_instance_admin,
    isInstanceOwner: data.is_instance_owner,
    capabilities: data.capabilities ?? [],
  };
}
