# schakl Commercial License

Copyright (c) schakl. All rights reserved.

This license governs the **licensed module and integration directories** of this repository
listed below. Everything else in this repository is licensed under the GNU Affero General
Public License v3.0 (see [LICENSE](LICENSE)).

## Covered directories

The directories of modules and integrations that declare a `sku` on their `ModuleDescriptor`
(issue #137), and their web counterparts.

The two are split below because they are different things (CLAUDE.md §6a) and because the
distinction survives outside the code: a **module** is a capability of this software, while an
**integration** is a conversation with a third party whose API, terms and continued existence are
not ours. Licensing them identically is deliberate — what is licensed is the code in this
repository either way — but a reader of this file should be able to tell which of the two they are
looking at without opening the directory.

`scripts/license-check.mjs` verifies both directions of this list (every covered directory is
named, and every named directory exists) and does not care which heading a path appears under.

### Core

- `apps/api/app/core/cloud/`
- `apps/api/app/core/mcp/`
- `apps/web/src/lib/cloud/`
- `apps/web/src/routes/(cloud)/`
- `apps/web/src/routes/(app)/settings/service-access/`

### Modules

- `apps/api/app/modules/automation/`
- `apps/api/app/modules/domains/`
- `apps/api/app/modules/hosting/`
- `apps/api/app/modules/hr/`
- `apps/api/app/modules/interactions/`
- `apps/api/app/modules/invoicing/`
- `apps/api/app/modules/leave/`
- `apps/api/app/modules/marketing/`
- `apps/api/app/modules/portal/`
- `apps/api/app/modules/projects/`
- `apps/api/app/modules/reporting/`
- `apps/api/app/modules/subscriptions/`
- `apps/api/app/modules/time/`
- `apps/api/app/modules/websites/`
- `apps/web/src/lib/modules/automation/`
- `apps/web/src/lib/modules/domains/`
- `apps/web/src/lib/modules/hosting/`
- `apps/web/src/lib/modules/interactions/`
- `apps/web/src/lib/modules/invoicing/`
- `apps/web/src/lib/modules/leave/`
- `apps/web/src/lib/modules/marketing/`
- `apps/web/src/lib/modules/portal/`
- `apps/web/src/lib/modules/projects/`
- `apps/web/src/lib/modules/reporting/`
- `apps/web/src/lib/modules/subscriptions/`
- `apps/web/src/lib/modules/time/`
- `apps/web/src/lib/modules/websites/`
- `apps/web/src/routes/(app)/domains/`
- `apps/web/src/routes/(app)/interactions/`
- `apps/web/src/routes/(app)/invoices/`
- `apps/web/src/routes/invoice/`
- `apps/web/src/routes/(app)/leave/`
- `apps/web/src/routes/(app)/marketing/`
- `apps/web/src/routes/(app)/projects/`
- `apps/web/src/routes/(app)/quotes/`
- `apps/web/src/routes/(app)/reports/`
- `apps/web/src/routes/(app)/subscriptions/`
- `apps/web/src/routes/(app)/time/`
- `apps/web/src/routes/(app)/websites/`
- `apps/web/src/routes/(app)/settings/automation/`
- `apps/web/src/routes/(app)/settings/hosting/`
- `apps/web/src/routes/(app)/settings/interaction-kinds/`
- `apps/web/src/routes/(app)/settings/invoicing/`
- `apps/web/src/routes/(app)/settings/leave/`
- `apps/web/src/routes/(app)/settings/marketing/`
- `apps/web/src/routes/(app)/settings/reporting/`
- `apps/web/src/routes/(app)/settings/subscriptions/`
- `apps/web/src/routes/(app)/settings/time-entry-types/`

### Integrations

- `apps/api/app/integrations/cloudflare/`
- `apps/api/app/integrations/google/`
- `apps/api/app/integrations/google_ads/`
- `apps/api/app/integrations/google_tag_manager/`
- `apps/api/app/integrations/mollie/`
- `apps/api/app/integrations/oxxa/`
- `apps/api/app/integrations/snelstart/`
- `apps/api/app/integrations/uptime/`
- `apps/api/app/integrations/wordpress/`
- `apps/web/src/lib/integrations/cloudflare/`
- `apps/web/src/lib/integrations/google/`
- `apps/web/src/lib/integrations/google_ads/`
- `apps/web/src/lib/integrations/google_tag_manager/`
- `apps/web/src/lib/integrations/mollie/`
- `apps/web/src/lib/integrations/oxxa/`
- `apps/web/src/lib/integrations/snelstart/`
- `apps/web/src/lib/integrations/uptime/`
- `apps/web/src/lib/integrations/wordpress/`
- `apps/web/src/routes/(app)/settings/cloudflare/`
- `apps/web/src/routes/(app)/marketing/tag-manager/`
- `apps/web/src/routes/(app)/settings/google-ads/`
- `apps/web/src/routes/(app)/settings/google/`
- `apps/web/src/routes/(app)/settings/gtm/`
- `apps/web/src/routes/(app)/settings/mollie/`
- `apps/web/src/routes/(app)/settings/oxxa/`
- `apps/web/src/routes/(app)/settings/snelstart/`
- `apps/web/src/routes/(app)/settings/uptime/`

Each covered directory carries a `LICENSE` file referring here. Code in these directories
published in repository history **before** this file was introduced — or, for a directory
added to this list later, before the commit that added it — remains available under the
AGPL-3.0 terms it was published under; this license governs all later versions.

`pnpm license:check` (CI and pre-commit) enforces the correspondence: a module that declares
a `sku` carries a marker on its API directory and its web counterpart, every marked directory
appears in the list above, every listed directory exists and is marked, and all markers read
alike. Route directories are the part no rule can derive from a module name — `invoicing`
covers `routes/(app)/invoices/`, `.../quotes/`, `.../settings/invoicing/` and the public
`routes/invoice/` (#304), which is not even under `(app)` — so adding a screen to a licensed
module is a judgement, and the check only keeps it honest once made.

## Grant

Subject to a valid schakl license key issued by the copyright holder, you are granted a
non-exclusive, non-transferable right to:

1. run the covered code as part of a schakl installation, for the number of
   installations/seats your license key states;
2. modify the covered code for use within your own licensed installation;
3. view and study the source.

## Restrictions

You may **not**, without a separate written agreement:

- use the covered code (or modifications of it) without a valid license key, except during
  the built-in trial window the software itself grants;
- redistribute the covered code or modifications of it, in source or binary form, as part
  of any product or service;
- remove, circumvent or disable the license-key mechanism except as the AGPL-licensed part
  of the software already permits for the AGPL-licensed parts.

Expiry of a license key does not terminate your right to **read and export your own data**;
the software keeps read and export functions available regardless of license state.

## Contributions

By submitting a contribution touching a covered directory you agree that the copyright
holder may license that contribution under this license and under future license terms for
the covered directories. (Contributions to everything else are accepted under AGPL-3.0.)

## No warranty

The covered code is provided "as is", without warranty of any kind. In no event shall the
copyright holder be liable for any claim, damages or other liability arising from its use.

---

*This is a plain-language commercial license written by the project; have it reviewed by
counsel before relying on it in high-stakes agreements.*
