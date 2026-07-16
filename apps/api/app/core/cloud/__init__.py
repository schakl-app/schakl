"""Cloud deployment core (epic #199) — business-licensed, see this directory's LICENSE.

Everything cloud-posture-specific lives here: the org-issued **service PIN** that gates the
instance owner's access to tenant data, the **provisioning API** driven by instance API keys
(plans: trial / standard / unlimited), and the **ingress sync** that renders Traefik router
fragments for verified custom domains (Let's Encrypt). The routers mount unconditionally so
the OpenAPI spec (and the generated web client) is posture-independent, but every route
carries :func:`app.core.cloud.deps.require_cloud` — on a self-hosted box the whole surface
answers 404, the same "doesn't advertise itself" posture as ``SCHAKL_INSTANCE_ADMIN_ENABLED``.
"""
