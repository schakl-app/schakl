"""Seed a fresh install with **one org** + owner + settings (CLAUDE.md §5).

Idempotent — safe to run on every deploy. Uses the same Argon2 hasher as FastAPI Users so the
seeded admin can log in locally. Org-scoped rows are inserted with the RLS GUC bound to the new
org (they are RLS-forced), proving the tenancy boundary from the write side too.

    uv run python -m app.seed
"""

from __future__ import annotations

import asyncio
import logging

from pwdlib import PasswordHash
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.core.models import Membership, Org, OrgSettings
from app.core.roles import Role
from app.db import async_session_maker, set_current_org

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vlotr.seed")

_password_hash = PasswordHash.recommended()


async def seed() -> None:
    async with async_session_maker() as session:
        # --- Org (tenant table; no RLS) ---
        org = await session.scalar(select(Org).where(Org.slug == settings.seed_org_slug))
        if org is None:
            org = Org(slug=settings.seed_org_slug, name=settings.seed_org_name)
            session.add(org)
            await session.flush()
            logger.info("Created org '%s'", org.slug)

        # --- Owner user (global identity; no RLS) ---
        user = await session.scalar(
            select(User).where(User.email == settings.seed_admin_email)
        )
        if user is None:
            user = User(
                email=settings.seed_admin_email,
                hashed_password=_password_hash.hash(settings.seed_admin_password),
                is_active=True,
                is_verified=True,
                is_superuser=True,
                full_name="Administrator",
            )
            session.add(user)
            await session.flush()
            logger.info("Created admin user '%s'", user.email)

        # --- Org-scoped rows: bind RLS to this org before inserting ---
        await set_current_org(session, org.id)

        membership = await session.scalar(
            select(Membership).where(
                Membership.org_id == org.id, Membership.user_id == user.id
            )
        )
        if membership is None:
            session.add(
                Membership(org_id=org.id, user_id=user.id, role=Role.OWNER.value)
            )
            logger.info("Granted OWNER membership")

        org_settings = await session.scalar(
            select(OrgSettings).where(OrgSettings.org_id == org.id)
        )
        if org_settings is None:
            session.add(
                OrgSettings(
                    org_id=org.id,
                    brand_name=settings.seed_org_name,
                    default_locale=settings.default_locale,
                    enabled_modules=list(settings.enabled_modules),
                )
            )
            logger.info("Created org_settings (brand='%s')", settings.seed_org_name)

        await session.commit()
        logger.info("Seed complete for org '%s'.", org.slug)


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
