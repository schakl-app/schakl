# Security Audit — schakl

**Scope:** full application security review of the schakl monorepo (FastAPI API + SvelteKit web,
multi-tenant, RLS + RBAC).
**Date:** 2026-07-16
**Branch:** `claude/strix-vuln-privilege-escalation-7xo0rs`
**Threat model:** an authenticated **low-privilege member of tenant A** attempting to (a) read or
write tenant B's data, (b) escalate privilege within A, (c) bypass authentication, or (d) abuse a
feature to reach the host/network/secrets. Severity is rated against that model.

## Methodology

1. **White-box review** — eight parallel adversarial auditors, each owning one attack surface:
   tenant-isolation/RLS, RBAC/permissions, auth/OIDC/impersonation, API-keys/MCP,
   injection/SSRF/file, web/SSR/XSS/CSRF, business-logic, and crypto/secrets/infra.
2. **Live sandbox** — a real Postgres cluster with a **non-superuser** application role (so
   `FORCE ROW LEVEL SECURITY` genuinely applies — the whole point) + Redis, migrated to head
   (89 tables, 84 RLS-forced — the 5 exempt are the sanctioned instance-level tables).
3. **Dynamic pentest** — an adversarial test suite (`apps/api/tests/test_audit_adversarial.py`)
   firing real cross-tenant and privilege-escalation requests through the full HTTP → tenancy →
   RLS → RBAC stack. The existing security suite was run first as a baseline (**green**).

## What is strong (verified, no action)

The security architecture is genuinely well-built; most crown-jewel invariants held under tracing
and live testing:

- **Tenant isolation.** RLS coverage is complete and structurally enforced (`test_tenancy_seams`);
  every org-scoped write goes through `TenantScopedRepository` (injects `org_id`), `org_id` cannot
  be mass-assigned, a spoofed `X-Forwarded-Host` still re-verifies membership through RLS, and
  import/export re-scopes to the acting org. Cross-tenant reads/writes were **not** achievable.
- **RBAC core.** `PermissionSet.has` is correct (exact membership, wildcard short-circuit, `:any`
  satisfies `:own`); deny-by-default is enforced by a non-vacuous lint + behavioural sweep; the
  last-role-manager lockout guard is sound.
- **No injection classes.** No SQLi (all raw `text()` uses bound params / fixed identifiers), no
  command exec, no unsafe deserialization, no XXE, no zip-slip, no path traversal (storage keys are
  server-generated UUIDs behind an `is_relative_to` guard).
- **Rich text / branding.** HTML is stripped server-side (`nh3.clean`) and sanitized client-side
  (DOMPurify with a `javascript:`-blocking URI policy); branding colors are regex-validated before
  reaching CSS.
- **License verification** (Ed25519) is checked before any claim is trusted; **API keys** use CSPRNG
  entropy, constant-time compare, mint-time scope capping, and per-request re-cap for personal keys;
  **MCP** proxies in-process through `require_context` and never forwards the caller credential to an
  external service. **CORS** middleware is absent (so no wildcard-origin + credentials pitfall); the
  error envelope leaks no stack traces.

## Findings summary

| ID | Severity | Title | Status |
|----|----------|-------|--------|
| F1 | **Critical** | Default `secret_key`, no production boot guard, publicly-known Compose default | **Fixed in branch** |
| F2 | **Critical** (priv-esc) | `members.member.write` can grant the `owner` role (wildcard `*`), incl. self-promotion | **Fixed in branch** |
| F3 | **Critical** | OIDC adopts a pre-existing account on an unverified email → account takeover (reaches the superuser owner) | **Fixed in branch** |
| F4 | **High** | Stored XSS via `javascript:` URL in company website + task links (member→admin escalation) | **Fixed in branch** (API-source) |
| F5 | High | SSRF via OIDC discovery URL — outbound guard skipped, follows redirects | Documented |
| F6 | High | SSRF via AI `base_url` — outbound guard skipped, response body reflected | Documented |
| F7 | Medium | BOLA on activity / notification entity feeds (within-tenant cross-module info leak) | Documented |
| F8 | Medium | SSRF DNS-rebinding TOCTOU on both outbound guards | Documented |
| F9 | Medium | Notification SSRF guard uses a weaker deny-list than the webhook guard | Documented |
| F10 | Medium | CSV formula/DDE injection on export | Documented |
| F11 | Medium | Secrets-at-rest key: unsalted SHA-256 KDF + auth-secret reuse | Documented |
| F12 | Medium | Traefik dashboard/API runs `insecure: true`, published on `:8080` | Documented |
| F13 | Medium | Containers run as root (no `USER`) | Documented |
| F14 | Medium | SSR web ships no security headers (CSP / XFO / XCTO / HSTS) | Documented |
| F15 | Medium | Auth/session/impersonation cookies default to non-`Secure` | Documented |
| F16 | Medium | No session revocation on logout / password-reset / impersonation-stop | Documented |
| F17 | Medium | Service-account API keys are never re-capped (privilege persists past demotion) | Documented |
| F18 | Medium | CSRF posture is SameSite-only with a same-origin API (multi-tenant cloud risk) | Documented |
| F19 | Low | Cross-module parent FK not validated (projects/tasks/time) — dangling ref + existence oracle | Documented |
| F20 | Low | Approver can set `hours_override` on their own leave while self-approval is off | Documented |
| F21 | Low | Email-verification token logged in plaintext at INFO | Documented |
| F22 | Low | Self-registration enabled by default + register-time account enumeration | Documented |
| F23 | Low | File upload trusts client `Content-Type` (SVG stored-XSS mitigated by `nosniff`+attachment) | Documented |
| F24 | Low | SMTP host/port SSRF (email-transport admin) | Documented |
| F25 | Low | MCP `tools/list`/`initialize` need no key (surface enumeration) | Documented |
| F26 | Low | Open redirect in `/set-locale` `/set-theme` `/set-format`; GET `/logout` (CSRF logout) | Documented |
| F27 | Info | Personal API key inherits owner's `is_superuser` (latent); MCP exclusion is path-prefix only | Documented |
| F28 | Info | Weak default DB creds / no Redis auth (internal network only); instance export includes password hashes; `debug=True` default (inert); `/docs` public; demo-mode outbound no-op not implemented | Documented |

---

## Fixed in this branch

### F1 — Default `secret_key` with no production boot guard *(Critical)*
`secret_key` signs the session JWT, password-reset/verify tokens, and the impersonation grant, and
derives the at-rest encryption key. The field default (`config.py`) is a literal, `infra/.env.example`
and `infra/compose.yaml` shipped a *publicly-known* fallback (`dev-secret-change-me-in-production`),
and nothing refused to boot on it. Anyone who knows the value forges a session for any user
(the `/members/lookup` directory hands out the owner's UUID to any member) and decrypts every stored
secret.

**Fix:** `config.py` now refuses to boot in production on a default/known/short secret
(`_guard_production_secrets`); `infra/compose.yaml` + `compose.tunnel.yaml` require the var
(`${SCHAKL_SECRET_KEY:?…}` — no fallback); `.env.example` documents generation and that the dev
placeholder only works in development.

### F2 — `members.member.write` can confer the `owner` wildcard *(Critical — privilege escalation)*
`update_member_role` (`PATCH /members/{id}`) and `invite_member` were gated only on
`members.member.write` (a team-management capability, separate from `settings.roles.manage`), yet
both accepted `role="owner"` — and `owner` stores `*`. With no anti-escalation or self-target check,
a holder of `members.member.write` (e.g. a tenant's custom "Office Manager" role) could self-promote
to owner or mint a new owner, defeating the roles API's deliberate "`*` is never assignable" rule.
`set_member_roles` (`PUT /members/{id}/roles`) had the same leak via the owner role id.

**Fix:** conferring `owner` now requires the role-administration capability `settings.roles.manage`
(`_guard_owner_grant`), not merely `members.member.write`. A custom "office manager" role holding only
`members.member.write` can no longer mint an owner; an admin/role-manager designating an owner stays
legal (intended, and covered by `test_change_role_and_last_role_manager_guard`). `PUT /members/{id}/roles`
already requires `settings.roles.manage`, so it is consistent without extra code.
**Verified:** `test_FIXED_members_write_cannot_escalate_to_owner` (self-promote and invite both 403;
a non-owner role assignment still works) — plus the existing members suite stays green.

### F3 — OIDC account takeover via unverified email *(Critical)*
`oidc_callback` matched an IdP-asserted `email` to an existing local `User` and issued that user's
session, with **no** `email_verified` check anywhere. On a permissive IdP (self-service signup, a
social connection), an attacker authenticates as `owner@agency.com` and captures the pre-existing
local `/setup` **superuser** account — no password, no verification.

**Fix:** `oidc.py` now requires the IdP to assert a verified email before adopting a *pre-existing*
account (`_email_verified`); brand-new JIT provisioning is unchanged.
**Verified:** `test_FIXED_oidc_requires_verified_email_to_adopt_account` (unverified adoption refused
with no session; verified adoption still works).
**Operator note:** all mainstream IdPs (Google, Entra, Okta, Auth0, Keycloak) assert `email_verified`.
Confirm yours does. The stronger long-term fix is to key SSO identity on `(iss, sub)` rather than email.

### F4 — Stored XSS via `javascript:` URLs *(High — member→admin escalation)*
`company.website` and task-link `url` accepted arbitrary strings and were rendered into an `href`
(the task-link normalizer's `"://"` heuristic even lets `javascript://…` through). A member plants a
`javascript:` payload; when an owner clicks it, it runs with the owner's session on the app origin.

**Fix:** a shared `reject_dangerous_url` (`app/core/urls.py`) refuses `javascript:`/`data:`/
`vbscript:`/`file:` schemes at the write path for both fields.
**Verified:** `test_FIXED_dangerous_url_schemes_rejected`.
**Defense-in-depth still recommended:** a `safeUrl()` on render and a Content-Security-Policy (F14).

---

## Documented findings (recommended, not auto-applied)

These are real but were left for review because they touch deployment posture, span multiple
files/tiers, or need product judgement. Each has a concrete fix.

**F5 / F6 — SSRF in OIDC discovery + AI `base_url` (High).** Both fetch a tenant-admin-supplied URL
with **no** private-address guard; OIDC follows redirects, AI reflects the response body (an
exfiltration oracle). Route every outbound call through one shared "resolve-once, reject non-global,
pin-the-IP, no-redirects" helper (reuse `automation/webhook.py:ensure_public_target`). Admin-gated,
hence High not Critical. Files: `app/core/auth/sso.py:239`, `app/core/ai/providers.py:251-259`.

**F7 — BOLA on entity feeds (Medium).** `GET /api/v1/activity?entity_type=&entity_id=` and the
notifications activity feed are gated only on a blanket `activity.read` / `notifications.notification.read`
(held by every role incl. read-only `client`) with no check that the caller may see *that* entity —
so a member reads the field-level change history of subscriptions/interactions/etc. they hold no read
permission for. Gate the trail on "can read this entity", not just the blanket read.
Files: `app/core/activity/router.py:27`, `app/modules/notifications/router.py:151`.

**F8 / F9 — SSRF guard weaknesses (Medium).** Both guards resolve-then-connect (DNS-rebinding TOCTOU);
the notification guard's deny-list (`is_private/loopback/link_local/reserved`) misses ranges that
`not ip.is_global` catches (`0.0.0.0`, CGNAT, IPv4-mapped IPv6). Pin the validated IP at connect time;
replace the deny-list with `not is_global`. Files: `app/modules/notifications/external.py:114-121`,
`app/modules/automation/webhook.py:62-98`.

**F10 — CSV injection (Medium).** Exported cells aren't neutralized against a leading `= + - @`.
Prefix a `'` in `impex/service.py:_cell`.

**F11 — Secrets-at-rest KDF (Medium).** Fernet itself is fine; the key is a bare unsalted SHA-256 of
`secret_key` (reused for both signing and encryption). Require a dedicated `encryption_key`, derive via
HKDF-SHA256 with a context string. File: `app/core/crypto.py:26`.

**F12 – F15 — Infra/transport posture (Medium).** Traefik `api.insecure: true` on `:8080`
(`infra/traefik/traefik.yml`); containers run as root (both Dockerfiles — add a non-root `USER`); no
security headers in `apps/web/src/hooks.server.ts` (add CSP, `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, HSTS); `auth_cookie_secure` defaults `False`
(`config.py:121` — default `True`/derive from production).

**F16 — No session revocation (Medium).** Stateless 7-day JWT; logout/reset/impersonation-stop only
clear the cookie. Add a per-user token version (or `sessions_valid_after`) bumped on password change /
logout. File: `app/core/auth/backend.py:25`.

**F17 — Service-account keys not re-capped (Medium).** Personal keys re-cap to the owner's live perms
every request; service-account keys keep their mint-time scopes forever, surviving the creator's
demotion/offboarding. Re-cap to the creator's current grants, or surface them in the offboarding flow.
File: `app/core/apikeys/auth.py:134-149`.

**F18 — CSRF thin (Medium, cloud model).** Cookie-authed API is same-origin with the web app; defense
is SameSite=Lax only (sibling tenant subdomains are same-site). Add an Origin allow-list / double-submit
token on state-changing routes; keep mutations JSON-only; consider host-isolating the API.

**F19 – F28 (Low / Info).** Cross-module parent-FK validation gap in projects/tasks/time (dangling ref +
500-based existence oracle — add the org-scoped parent check the other modules already use); leave
`hours_override` self-approval carve-out missing; verification token logged (`manager.py:56`);
self-registration default-on + register enumeration; upload content-type trusted (SVG XSS mitigated
today by `nosniff`+attachment — keep SVG out of any inline set); SMTP SSRF; MCP `tools/list` unauth
(openapi already public); open redirect in the `set-*` routes + GET `/logout`; weak default DB creds /
no Redis auth (internal-only); instance export includes password hashes (superuser-gated); `debug=True`
default (inert — never wired to FastAPI); `/docs` public; demo-mode outbound no-op documented but not
implemented (unreachable by a default demo member). Details and line numbers are in the per-area notes
above and in the commit for the fixed items.

---

## Tests added

`apps/api/tests/test_audit_adversarial.py` — cross-tenant CRUD is 404 on every verb; foreign-host
session is 403; `org_id`/`id` body fields can't reassign tenant; plain members can't reach role admin;
the wildcard is unassignable; cross-tenant role access is 404; plus the three fix-verification tests
(F2/F3/F4) and two finding-demonstrations (the default-secret forgery and the member-directory
exposure that feeds it).
