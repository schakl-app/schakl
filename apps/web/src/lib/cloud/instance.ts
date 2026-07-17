/**
 * Cloud instance helpers (epic #199). Business-licensed — see this directory's LICENSE.
 *
 * The instance console lives on the apex host, where no org resolves — so the tenant-bound
 * `/meta/me` never answers there. These helpers speak to the posture endpoint
 * (`/meta/instance`) and the console identity endpoint (`/instance/me`) instead.
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
  isInstanceAdmin: boolean;
  isInstanceOwner: boolean;
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
  };
}
