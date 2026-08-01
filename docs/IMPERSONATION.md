# Impersonation — signing in as someone else

> Two features, one mechanism. Read this before touching either, and before adding a third kind.

The platform lets one person operate as another, deliberately and visibly:

| Kind | Who | As whom | Gated on | Recorded on |
|---|---|---|---|---|
| `instance` | an instance owner / admin (issue #26) | any member of any org | `instance.impersonate` (a capability, not a tenant permission) | the instance audit log (`instance_audit_log`) |
| `portal` | agency staff, inside their own org (#296) | a **client contact's portal login**, and nothing else | `contacts.portal.impersonate` (a tenant permission, §15) | the contact's activity trail (`activity_log`, §16) |

Everything below the two rows is shared.

## The grant

`app/core/impersonation.py`. An impersonation is a **short-lived, audience-bound JWT in its own
cookie** (`schakl_impersonate`), set *next to* — never instead of — the impersonator's session
cookie:

- Authentication stays the real principal. `require_context` swaps only the **effective** user,
  and only when the request carries both a valid session and a grant naming that session's user.
  A stolen grant cookie on its own authenticates nothing; presented with someone else's session
  it applies nothing.
- **Permissions resolve for the target, below the swap.** An impersonated session is never more
  powerful than the account it entered — `is_superuser` never implies `*`, and the instance-admin
  flags on `/meta/me` go false while impersonating.
- The window is clamped by `SCHAKL_IMPERSONATION_MAX_MINUTES` (default 60).
- The `kind` claim decides what may still kill the grant: `SCHAKL_INSTANCE_ADMIN_ENABLED=false`
  kills every `instance` grant instantly, while a `portal` grant is ordinary tenant business and
  keeps working on a box with the cross-tenant admin surface switched off (i.e. every self-hosted
  one).

**The capability is checked where the grant is issued, never on the hot path.** `read_impersonation`
runs on every request on a tenant host, so it does no queries. The consequence is real and worth
stating: revoking the permission does not kill a grant already in flight — it lapses within one
window. Deactivating the account (or, for `instance`, the admin flag) is the immediate lever.

### Crossing hostnames (`instance` only)

Cookies are host-scoped, and the console generally runs on another host than the tenant, so an
`instance` impersonation is a **single-use handoff ticket** redeemed on the target host
(`app/core/instance/impersonation.py`, #288). A `portal` impersonation never needs it: staff are
already on their own tenant's hostname, so the grant is simply set beside the session that is
there.

## What makes `portal` safe to hand a tenant

An instance owner is trusted with everything by definition. A tenant admin is not, so the portal
kind carries three extra guarantees (`app/modules/contacts/portal.py`):

1. **The target can only ever be a portal login.** The endpoint names a *contact*, and the login
   is `contacts.user_id` — a link only the invite flow creates, and that flow refuses an address
   that already has an account. There is no input that names an arbitrary user, so "sign in as
   the owner" is not a request that can be expressed.
2. **It can never gain the caller a permission** (`PermissionSet.covers`). Roles are
   tenant-editable, so "it's only a client" is not by itself a bound on what the client role
   holds; the target's effective set must already be covered by the caller's, or the answer is
   `403 errors.impersonation_escalation`. A superuser target is refused outright.
3. **It obeys the company horizon.** The contact is loaded through the tenant repository, so a
   membership scoped to a company group (#191/#285) can only enter the contacts of its own
   clients; anything else answers 404, like every other read.

Nesting is refused (`409 errors.impersonation_nested`): an impersonated session opening a second
grant would launder one identity into another with only the first crossing recorded.

## Being visible is half the feature

- **A banner on every screen**, with a one-click stop (`(app)/+layout.svelte`, from `/meta/me`'s
  `impersonated_by` / `impersonation_kind`).
- **Start and stop are recorded** — on the instance audit log, or on the contact's activity trail
  as `portal_impersonation_started` / `portal_impersonation_stopped`.
- **Every write made while it runs names the impersonator.** `activity_log` and `task_activities`
  both carry `impersonator_user_id` + `impersonator_name`, written by the service that records the
  change and rendered as a "via …" chip beside the actor. Without it the trail would say the
  client did it, which is exactly the fact an audit exists to correct. The name is snapshotted at
  write time and the FK is `ON DELETE SET NULL`, like the actor's (issue #64): a departed
  impersonator must not quietly become nobody.

The stop endpoint deliberately declares **no permission**. It runs as the impersonated account,
which by definition holds none of the impersonator's permissions — gating the only way out behind
a permission the account cannot have would trap someone inside the session. It authorizes nothing
beyond ending itself, and `tests/test_rbac_deny_by_default.py` carries that reasoning in its
allowlist.

Which stop endpoint the web calls comes from `locals.user.impersonationKind` — from the API,
never from a hidden form field. Letting the browser pick which trail its own stop lands on is
precisely the choice an audit trail exists to take away.

## Adding a third kind

Add it to `KINDS`, decide what still kills it in `read_impersonation`, give it a recording
surface (an existing trail, not a new one), and answer the three questions above about the target
before writing a line of it. If a kind cannot answer "what stops the caller gaining a permission
they don't have?", it is not ready to exist.

## Tests

`apps/api/tests/test_portal_impersonation.py` (the portal kind, including escalation, nesting,
horizon, tenant isolation and both trails) and `apps/api/tests/test_instance_admin.py` (the
instance kind, including the handoff's single-use and host-binding rules).
