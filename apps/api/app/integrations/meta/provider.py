"""This integration's answer to ``app.core.metagraph.assets``. Business-licensed — see LICENSE.

Thin on purpose: a borrower asks one question and never learns this integration's tables.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.metagraph import MetaAssetRef, MetaCredentials, register_meta_assets
from app.integrations.meta.models import MetaAsset
from app.integrations.meta.service import MetaService


def asset_ref(row: MetaAsset) -> MetaAssetRef:
    return MetaAssetRef(
        id=row.id,
        kind=row.kind,
        external_id=row.external_id,
        name=row.name,
        company_id=row.company_id,
        currency=row.currency,
        timezone=row.timezone,
        active=row.active,
        status=row.status,
    )


class MetaAssets:
    async def assets_for_company(
        self, ctx: Any, company_id: uuid.UUID, *, kind: str | None = None
    ) -> list[MetaAssetRef]:
        rows = await MetaService(ctx).list_assets(
            kind=kind, company_id=company_id, active_only=True
        )
        return [asset_ref(row) for row in rows]

    async def call_credentials(
        self, ctx: Any, asset_id: uuid.UUID
    ) -> tuple[MetaAssetRef, MetaCredentials]:
        service = MetaService(ctx)
        row = await service.get_asset(asset_id)
        return asset_ref(row), await service.credentials_for(row)


def install() -> None:
    register_meta_assets(MetaAssets())
