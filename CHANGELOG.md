# Changelog

## v0.12.0 — 2026-07-17

A large release: five parallel work streams merged — the security audit remediation, two-factor authentication, the invoicing and quotes module, the cloud (multi-org) posture, and the client-hub batch covering issues #190 through #198 plus the portal, HR and mobile work that followed it.

### Security

- Full security audit of the API and web app (#29): tenant isolation, the RBAC core, the injection surface, rich-text and branding sanitization, and license/API-key handling all held. Four critical/high findings are fixed in this release; the remaining findings are documented with remediations in `SECURITY_AUDIT.md`, and an adversarial test suite now runs with the normal CI so the audit is a ratchet rather than a snapshot.
- The API refuses to boot in production on a default, publicly known, or short `SECRET_KEY`. See the upgrade notes below.
- Conferring the `owner` role now requires `settings.roles.manage`, closing a privilege-escalation path from `members.member.write`.
- OIDC sign-in only adopts a pre-existing local account when the IdP asserts `email_verified`, closing an account-takeover path via a permissive IdP.
- `javascript:`, `data:` and `vbscript:` URL schemes are rejected at the API for company websites and task links (stored XSS).
- A record's activity trail now also requires the entity's own read permission on top of `activity.read`.

### Two-factor authentication

- TOTP with QR enrollment, ten single-use backup codes, and an optional SMS factor (instance-configured gateway; only ever an add-on to TOTP). Login becomes a two-step challenge for enrolled accounts; all verify paths share brute-force damping.
- Org admins can reset a member's second factor from Instellingen → Gebruikers (audited); an org that enforces SSO keeps MFA at the IdP.
- Self-service email change guarded by the current password; the unguarded `email` field on the bare profile update is closed.

### Invoicing and quotes

- A native `invoicing` module (#207): sales invoices and quotes raised inside the CRM, wired into unbilled approved time, subscription cycles, and the new company billing-identity fields (#11 — VAT/CoC and postal address, snapshotted onto issued documents).
- Tenant-configurable locale-dependent tax rates, document templates, automatic payment reminders, per-document currency and locale, and an accounting seam for a bookkeeping package to take over.

### Cloud posture (business-licensed)

- `SCHAKL_DEPLOYMENT=cloud` turns an installation into the operator-run multi-org posture: an instance console on the apex host, a provisioning API behind instance API keys, org plans (trial, standard, unlimited) with a daily trial-expiry cron, and an included instance e-mail transport orgs can opt into.
- Service PIN: the instance owner cannot open an org's data until an org admin generates a time-boxed, revocable PIN (#199, partial).
- Wildcard main-domain ingress plus customer custom domains via CNAME with automatic per-domain TLS (#202).
- Self-hosted behaviour is unchanged; the cloud surface returns 404 unless the posture is enabled.

### Client portal and per-task visibility

- Contacts can be invited to a client portal login (#193): a reduced shell, a curated dashboard with the client's own logo (#196), and a data horizon limited to their companies.
- Tasks carry a "visible to client" flag: portal logins see exactly the flagged tasks of their companies, can comment on them, and never see the activity trail, uploads, or staff panels. Existing installations receive the client comment grant through a data migration.

### Client hub

- Quick-create from the client page: permission-gated "new" affordances on the tasks, projects, domains, hosting and subscriptions panels open the module's own create form with the client preselected; a domain row links to its website or offers creating one.
- Company groups (#191): a per-membership company data horizon, enforced in the tenant-scoped repository and visible on the users screen.

### HR

- A new `hr` module with a personal page per employee, reached from the profile menu: leave balance, current contract, and a per-category dossier (contract copy, growth plans, bonus agreements, benefits, CAO). Dossier managers upload and remove documents; every filing lands on the activity trail.

### Marketing

- Per-client marketing tab layout editor (#192): reorder, hide and relabel tiles per source, enforced server-side.
- Marketing links can attach to a specific client website; pickers offer the client's websites and the marketing tab groups per site.
- Fixed the mobile drill-down overflow (#195).

### Tasks

- A strict use-versus-edit split on the task detail: the default surface is working the task (status, checklist ticking, comments, planning); everything structural lives behind the edit mode, and empty structural sections no longer render.
- Ticking the last open to-do offers to move the task to its terminal status, and explains the closing contact-moment requirement where one applies.
- Task references in rich text via `#` with autocomplete (#197); mobile fixes for the vanishing row title and the filter bar.

### Platform

- S3-compatible object storage via instance environment variables (`SCHAKL_STORAGE_S3_*`), with per-file backend dispatch so existing local files keep working (#190).
- Real PWA and iOS home-screen icons derived per tenant from an uploaded app icon (#198).
- My Day dashboard tiles keep equal spacing regardless of their heights.

### Upgrade notes

- `SCHAKL_SECRET_KEY` is now required in production: an installation still running the former default key will refuse to boot. Set a strong value before upgrading.
- Fourteen additive database migrations apply automatically on upgrade; the chain has a single head and a real downgrade path.
- The deprecated `marketing_company_settings.show_key_events` column stays readable this release and will be dropped in the next one.
- New orgs enable the `hr` and `invoicing` modules by default; existing orgs enable them under Instellingen → Modules.
