"""What a subscription custom field may be attached to (§13, ``core/customfields/scoping``).

Two dimensions, each named by the **attribute** an agreement carries it under, which is the
seam's contract: ``subscription_type_id`` and ``subscription_template_id``. A field scoped to
the *Hosting* type shows on every hosting agreement; one scoped to the *Hosting Pro* standard
subscription shows on the agreements made from that preset; one scoped to both shows on either.

The providers answer through the module's own tenant-scoped services, so a value the settings
screen may save is by construction one of this org's rows. They gate on the module's read
permission themselves — a manager who may edit custom fields but may not read subscriptions is
offered no options and therefore cannot save a scope, which is the honest answer rather than a
list of ids they may not see. Deactivated types are listed (flagged) so relabelling a field
scoped to one does not 422 on a value the tenant never touched.
"""

from __future__ import annotations

from app.core.customfields.scoping import CustomFieldScopeSpec, ScopeOption
from app.core.tenancy import RequestContext

READ_PERMISSION = "subscriptions.subscription.read"


async def type_options(ctx: RequestContext) -> list[ScopeOption]:
    if not ctx.can(READ_PERMISSION):
        return []
    from app.modules.subscriptions.service import SubscriptionTypeService

    types = await SubscriptionTypeService(ctx).list(include_inactive=True)
    return [
        ScopeOption(value=str(t.id), label_i18n=dict(t.label_i18n or {}), active=bool(t.active))
        for t in types
    ]


async def template_options(ctx: RequestContext) -> list[ScopeOption]:
    if not ctx.can(READ_PERMISSION):
        return []
    from app.modules.subscriptions.service import SubscriptionTemplateService

    templates = await SubscriptionTemplateService(ctx).list()
    return [
        ScopeOption(value=str(t.id), label_i18n={"nl": t.name, "en": t.name}) for t in templates
    ]


SUBSCRIPTION_SCOPES: tuple[CustomFieldScopeSpec, ...] = (
    CustomFieldScopeSpec(
        key="subscription_type_id",
        label_key="subscriptions.scope.type",
        options=type_options,
    ),
    CustomFieldScopeSpec(
        key="subscription_template_id",
        label_key="subscriptions.scope.template",
        options=template_options,
    ),
)
