"""Instance administration (issue #26): org lifecycle, impersonation, data portability.

Everything in this package operates **across** tenants, which is exactly what the rest of
the codebase must never do. The rules that keep that safe:

* the whole surface hides behind ``guard.require_instance_admin`` — off by default
  (``VLOTR_INSTANCE_ADMIN_ENABLED``) and restricted to instance owners
  (``users.is_superuser``, a principal distinct from any org's ``owner`` role);
* every cross-tenant read goes through ``repo`` — the one sanctioned unscoped crossing;
* every mutation writes an ``instance_audit_log`` row via ``audit``;
* org-scoped rows are still only touched with the RLS GUC bound to that org.
"""
