"""Planned posts: writing them, standing behind them, and letting them go.

The lifecycle, and who may move a post along it::

    draft ──offer──▶ review ──schedule──▶ scheduled ──(worker)──▶ publishing ──▶ published
      ▲                │                      │                                  partial
      └────────────────┴──────unschedule──────┘                                  failed

**Two permissions, and the line is the audience.** ``meta.post.write`` moves a post between
``draft`` and ``review`` — words nobody outside the building can read. ``meta.post.publish``
is asked for everything that ends with a client's followers reading them: scheduling,
publishing now, changing a post that is already scheduled, taking one back.

**Scheduling is where the permission is asked** (#335): the worker that publishes at the due
time runs as the system and is nobody, so a check at that moment would be no check at all. The
approver is snapshotted on the row for the same reason.

**A post is for one client.** Its channels are that client's assets (or the agency's own, for
a post that has no client), and a post that named two clients' Pages would be a post with two
answers to "whose is this" — refused, naming the field.
"""

from __future__ import annotations

import base64
import binascii
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select

from app.config import settings
from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.jobs import enqueue
from app.core.metagraph import KIND_AD_ACCOUNT, KIND_INSTAGRAM, MetaError
from app.core.sorting import apply_sort
from app.core.storage.service import FileService, check_upload
from app.core.timezone import as_instant, day_window, org_zoneinfo
from app.core.urls import reject_dangerous_url
from app.errors import AppError
from app.integrations.meta import media as media_lib
from app.integrations.meta.checks import (
    CheckAsset,
    Issue,
    channel_of,
    check_post,
    has_errors,
    schedule_issues,
)
from app.integrations.meta.media import MediaItem, read_media
from app.integrations.meta.models import (
    EDITABLE_POST_STATUSES,
    WORKING_POST_STATUSES,
    Channel,
    CredentialStatus,
    MetaAsset,
    MetaCredential,
    MetaPost,
    MetaPostTarget,
    PostFormat,
    PostStatus,
    Scheduler,
    TargetStatus,
)
from app.integrations.meta.publisher import Publisher
from app.integrations.meta.service import MetaService, capabilities

_ENTITY = "meta_post"
#: What an edit records. The internal note is deliberately absent: it is the team's own
#: scratch space, and a trail line per keystroke of it would bury the decisions (§16).
# ``status`` is absent on purpose: every move between statuses is a named act with its own
# line ("approved and planned for …"), and "Status: Concept → Ingepland" under it is the
# same fact a second time.
_TRACKED = ("company_id", "format", "body", "link", "scheduled_at")

#: The job "publish now" fires. Named here because the API enqueues it by name.
JOB_PUBLISH_POST = "meta_publish_post"

#: Statuses a post may be deleted in. A post that reached a channel is a record of what was
#: said in a client's name, and a record is not deleted.
DELETABLE_STATUSES = frozenset(
    {
        PostStatus.DRAFT.value,
        PostStatus.REVIEW.value,
        PostStatus.CANCELLED.value,
        PostStatus.FAILED.value,
    }
)


@dataclass
class PostView:
    """A post with what a reader needs beside it, gathered in a fixed number of queries."""

    post: MetaPost
    targets: list[MetaPostTarget]
    assets: dict[uuid.UUID, MetaAsset]
    issues: list[Issue]
    media: list[MediaItem]


class PostService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.meta = MetaService(ctx)
        self.publisher = Publisher(ctx)
        self.activity = ActivityService(ctx)

    # --- reads ------------------------------------------------------------------------------- #

    async def list(
        self,
        *,
        statuses: set[str] | None = None,
        company_id: uuid.UUID | None = None,
        asset_id: uuid.UUID | None = None,
        channel: str | None = None,
        q: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
        count: bool = True,
        sort: str | None = None,
    ) -> tuple[list[MetaPost], int]:
        """A page of posts and the total that page is a page *of*.

        ``statuses=None`` is **everything** — the endpoint's own default is never the narrowed
        one (CLAUDE.md §9, #329): the screen picks the working set and says so with a pill.
        """
        repo = self.ctx.repo(MetaPost)
        conditions = []
        if statuses:
            conditions.append(MetaPost.status.in_(statuses))
        if company_id is not None:
            conditions.append(MetaPost.company_id == company_id)
        if q:
            needle = f"%{q.strip()}%"
            conditions.append(or_(MetaPost.body.ilike(needle), MetaPost.notes.ilike(needle)))
        when = func.coalesce(MetaPost.published_at, MetaPost.scheduled_at)
        if date_from is not None or date_to is not None:
            zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
            lo, hi = day_window(date_from, date_to, zone)
            if lo is not None:
                conditions.append(when >= lo)
            if hi is not None:
                conditions.append(when < hi)
        if asset_id is not None or channel is not None:
            sub = select(MetaPostTarget.post_id).where(MetaPostTarget.org_id == self.ctx.org.id)
            if asset_id is not None:
                sub = sub.where(MetaPostTarget.asset_id == asset_id)
            if channel is not None:
                sub = sub.where(MetaPostTarget.channel == channel)
            conditions.append(MetaPost.id.in_(sub))

        # ``?sort=when`` / ``?sort=-when``, against an allow-list (``app/core/sorting.py``). A
        # post with no time yet sorts last whichever way the list runs: it is the one with the
        # least to say about *when*, and sorting it first buries next week under drafts.
        stmt = apply_sort(
            repo.scoped_select().where(*conditions),
            sort,
            {"when": when, "created": MetaPost.created_at, "status": MetaPost.status},
            default=when.asc().nulls_last(),
            tiebreak=MetaPost.created_at.desc(),
        )
        rows = list((await self.ctx.session.scalars(stmt.limit(limit).offset(offset))).all())
        total = len(rows)
        if count:
            total = int(
                await self.ctx.session.scalar(repo.scoped_count_select().where(*conditions)) or 0
            )
        return rows, total

    async def counts(self, *, company_id: uuid.UUID | None = None) -> dict[str, int]:
        """How many posts stand in each status — one grouped query, horizon carried."""
        repo = self.ctx.repo(MetaPost)
        stmt = (
            select(MetaPost.status, func.count())
            .where(MetaPost.org_id == self.ctx.org.id)
            .group_by(MetaPost.status)
        )
        if company_id is not None:
            stmt = stmt.where(MetaPost.company_id == company_id)
        horizon = repo.horizon_condition()
        if horizon is not None:
            stmt = stmt.where(horizon)
        return {status: int(n) for status, n in (await self.ctx.session.execute(stmt)).all()}

    async def get(self, post_id: uuid.UUID) -> MetaPost:
        row = await self.ctx.session.scalar(
            self.ctx.repo(MetaPost).scoped_select().where(MetaPost.id == post_id)
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def views(self, posts: list[MetaPost], *, with_issues: bool = True) -> list[PostView]:
        """Every post with its deliveries, its assets and what is wrong with it.

        Three queries whatever the page size — targets, assets, credentials — because the
        caller is a list: an endpoint that is one query at three posts and one per post at
        three hundred passes every functional test either way (docs/PERFORMANCE.md).
        """
        if not posts:
            return []
        ids = [post.id for post in posts]
        targets = list(
            (
                await self.ctx.session.scalars(
                    select(MetaPostTarget)
                    .where(
                        MetaPostTarget.org_id == self.ctx.org.id,
                        MetaPostTarget.post_id.in_(ids),
                    )
                    .order_by(MetaPostTarget.channel, MetaPostTarget.created_at)
                )
            ).all()
        )
        by_post: dict[uuid.UUID, list[MetaPostTarget]] = {}
        for target in targets:
            by_post.setdefault(target.post_id, []).append(target)
        assets = await self._assets({t.asset_id for t in targets})
        check_assets = await self.check_assets(list(assets.values())) if with_issues else {}
        out = []
        for post in posts:
            mine = by_post.get(post.id, [])
            items = read_media(post.media)
            issues: list[Issue] = []
            if with_issues and post.status in EDITABLE_POST_STATUSES:
                issues = check_post(
                    format=post.format,
                    body=post.body,
                    link=post.link,
                    media=items,
                    assets=[check_assets[t.asset_id] for t in mine if t.asset_id in check_assets],
                    overrides={t.asset_id: t.body_override for t in mine if t.body_override},
                )
            out.append(PostView(post=post, targets=mine, assets=assets, issues=issues, media=items))
        return out

    async def view(self, post: MetaPost) -> PostView:
        return (await self.views([post]))[0]

    # --- writing ----------------------------------------------------------------------------- #

    async def create(
        self,
        *,
        asset_ids: list[uuid.UUID],
        format: str = PostFormat.POST.value,
        body: str = "",
        link: str | None = None,
        notes: str = "",
        scheduled_at: datetime | None = None,
        overrides: dict[uuid.UUID, str] | None = None,
    ) -> MetaPost:
        """A new draft. Reaches nobody, so it asks for the drafting key and nothing more."""
        self.ctx.require("meta.post.write")
        assets = await self._destinations(asset_ids)
        company_id = _one_client(assets)
        self.ctx.repo(MetaPost)._guard_company_write({"company_id": company_id})
        post = MetaPost(
            org_id=self.ctx.org.id,
            company_id=company_id,
            format=_format(format),
            body=body,
            link=_link(link),
            notes=notes,
            scheduled_at=await self._instant(scheduled_at),
            status=PostStatus.DRAFT.value,
            created_by_user_id=self._user_id(),
            created_by_name=self._user_name(),
        )
        self.ctx.session.add(post)
        await self.ctx.session.flush()
        await self._set_targets(post, assets, overrides or {})
        await self.activity.record_created(
            _ENTITY,
            post.id,
            {"channels": sorted({channel_of(a.kind) or "" for a in assets})},
        )
        return post

    async def update(
        self,
        post: MetaPost,
        *,
        values: dict[str, Any],
    ) -> MetaPost:
        """Change a post that has not gone out. ``values`` holds only what the caller named
        (§18: absent means leave alone).

        A **scheduled** post is changed by whoever may schedule one, because the change is
        what will be published. Where Meta's own scheduler already holds a delivery it is
        taken back first — and if Meta will not let go, the edit is refused whole, with
        nothing changed on either side.
        """
        if post.status not in EDITABLE_POST_STATUSES:
            raise AppError("meta_post_locked", "errors.meta_post_locked", status_code=409)
        scheduled = post.status == PostStatus.SCHEDULED.value
        self.ctx.require("meta.post.publish" if scheduled else "meta.post.write")

        before = snapshot(post, _TRACKED)
        assets: list[MetaAsset] | None = None
        if "asset_ids" in values:
            assets = await self._destinations(values["asset_ids"])
            self.ctx.repo(MetaPost)._guard_company_write({"company_id": _one_client(assets)})
        new_media: list[MediaItem] | None = None
        if "media" in values:
            new_media = await media_lib.resolve_media(
                self.ctx, post.id, values["media"] or [], read_media(post.media)
            )
        when = post.scheduled_at
        if "scheduled_at" in values:
            when = await self._instant(values["scheduled_at"])
        if scheduled:
            if when is None:
                raise _invalid("scheduled_at", "meta.issue.no_time")
            problems = schedule_issues(when, datetime.now(UTC))
            if problems and when != post.scheduled_at:
                raise _invalid("scheduled_at", problems[0].code)
            await self.meta.require_writes_enabled()
            await self.publisher.withdraw(post)

        if "format" in values:
            post.format = _format(values["format"])
        if "body" in values:
            post.body = values["body"] or ""
        if "link" in values:
            post.link = _link(values["link"])
        if "notes" in values:
            post.notes = values["notes"] or ""
        if new_media is not None:
            post.media = [item.as_json() for item in new_media]
        post.scheduled_at = when
        if assets is not None:
            post.company_id = _one_client(assets)
            await self._set_targets(post, assets, values.get("overrides") or {})
        elif "overrides" in values:
            await self._set_overrides(post, values["overrides"] or {})
        await self.ctx.session.flush()

        if scheduled:
            # The post that will go out is a different post now, so it is judged again. A
            # change that makes it unpublishable is refused rather than left to fail at nine.
            view = await self.view(post)
            if has_errors(view.issues):
                raise _not_ready(view.issues)
        # A draft is being *written*: the composer saves it a moment after every pause, and a
        # trail line per pause buries the five that matter (offered, approved, planned,
        # published, failed). From the moment somebody else has been asked to look at it,
        # a change to the words is a change to what they were shown, and is written down.
        if post.status != PostStatus.DRAFT.value:
            await self.activity.record_update(_ENTITY, post.id, before, snapshot(post, _TRACKED))
        return post

    async def attach_image(
        self, post: MetaPost, *, filename: str, content_type: str, data: str, alt: str = ""
    ) -> MetaPost:
        """Store a picture and put it at the end of the post's media, in one step.

        It rides two capabilities and asks for both (#314): the post's own edit gate, which
        ``update`` applies, and the file store's, because a key that may write posts and may
        not write files has been told so on purpose.
        """
        if post.status not in EDITABLE_POST_STATUSES:
            raise AppError("meta_post_locked", "errors.meta_post_locked", status_code=409)
        scheduled = post.status == PostStatus.SCHEDULED.value
        self.ctx.require("meta.post.publish" if scheduled else "meta.post.write")
        self.ctx.require("files.file.write")
        if content_type not in media_lib.UPLOAD_IMAGE_TYPES:
            raise _invalid("content_type", "errors.meta_media_type")
        payload = data
        if payload.startswith("data:"):
            _, _, payload = payload.partition(",")
        # The encoded length is checked before the decode: a body that cannot fit under the
        # ceiling never costs the work the ceiling bounds (§17).
        if len(payload) > settings.upload_max_bytes * 4 // 3 + 4:
            raise AppError(
                "validation",
                "errors.upload_too_large",
                status_code=413,
                fields={"data": "errors.upload_too_large"},
                details={"limit_bytes": settings.upload_max_bytes},
            )
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise _invalid("data", "errors.invalid_base64") from None
        check_upload(content_type, len(raw))
        stored = await FileService(self.ctx).create(
            filename=filename,
            content_type=content_type,
            stream=io.BytesIO(raw),
            size_bytes=len(raw),
            entity_type=_ENTITY,
            entity_id=post.id,
        )
        media = [item.as_json() for item in read_media(post.media)]
        media.append({"kind": "image", "file_id": str(stored.id), "alt": alt})
        return await self.update(post, values={"media": media})

    async def offer(self, post: MetaPost) -> MetaPost:
        """Hand a draft to whoever may schedule it."""
        self.ctx.require("meta.post.write")
        if post.status != PostStatus.DRAFT.value:
            raise AppError("meta_post_locked", "errors.meta_post_locked", status_code=409)
        return await self._move(post, PostStatus.REVIEW.value, "meta.post_offered")

    async def recall(self, post: MetaPost) -> MetaPost:
        """Take a post back from review to keep writing it."""
        self.ctx.require("meta.post.write")
        if post.status != PostStatus.REVIEW.value:
            raise AppError("meta_post_locked", "errors.meta_post_locked", status_code=409)
        return await self._move(post, PostStatus.DRAFT.value, "meta.post_recalled")

    async def schedule(
        self, post: MetaPost, *, scheduled_at: datetime | None = None, now: bool = False
    ) -> MetaPost:
        """Approve a post and give it a time — or send it out at once.

        Refused, naming every problem, while the post has one: a post that cannot be published
        must not be *accepted* for publishing, because the person who would have fixed it is
        in front of the screen now and will not be at the due time.
        """
        self.ctx.require("meta.post.publish")
        if post.status not in (
            PostStatus.DRAFT.value,
            PostStatus.REVIEW.value,
            PostStatus.SCHEDULED.value,
        ):
            raise AppError("meta_post_locked", "errors.meta_post_locked", status_code=409)
        await self.meta.require_writes_enabled()
        moment = datetime.now(UTC)
        when = moment if now else await self._instant(scheduled_at) or post.scheduled_at
        view = await self.view(post)
        problems = [*view.issues, *([] if now else schedule_issues(when, moment))]
        if has_errors(problems):
            raise _not_ready(problems)
        if post.status == PostStatus.SCHEDULED.value:
            await self.publisher.withdraw(post)

        facebook = Scheduler.SCHAKL.value if now else await self.meta.scheduler()
        for target in view.targets:
            if target.status in (TargetStatus.PUBLISHED.value, TargetStatus.CANCELLED.value):
                continue
            target.status = TargetStatus.PENDING.value
            # Decided now and kept: a setting changed next week must not leave one post both
            # in our queue and in Meta's planner.
            target.scheduler = (
                facebook if target.channel == Channel.FACEBOOK.value else Scheduler.SCHAKL.value
            )
            target.attempts = 0
            target.claim_id = None
            target.claimed_at = None
            target.container_id = None
            target.last_error = None
            target.last_error_code = None
        post.scheduled_at = when
        post.status = PostStatus.SCHEDULED.value
        post.approved_by_user_id = self._user_id()
        post.approved_by_name = self._user_name()
        post.approved_at = moment
        await self.ctx.session.flush()
        # One line, and it carries the time: "changed Planned for: — → 09:00" under
        # "approved and planned for 09:00" is the same fact twice.
        await self.activity.record(
            _ENTITY,
            post.id,
            "meta.post_published_now" if now else "meta.post_scheduled",
            {"scheduled_at": when.isoformat() if when else None},
        )
        await self._wake_worker(post)
        return post

    async def unschedule(self, post: MetaPost) -> MetaPost:
        """Take a post back to a draft: a scheduled one, or one that failed everywhere.

        Nothing has gone out and nothing will. The second case is what makes a refusal
        fixable: Meta turned the words down, and the only other ways on were to send the same
        words again or to retype them into a copy. A post that landed on *any* channel is
        not taken back — it is live there, and a draft of a live post is a second post.
        """
        self.ctx.require("meta.post.publish")
        targets = await self.publisher.targets(post.id)
        failed = post.status == PostStatus.FAILED.value and not any(
            t.status in (TargetStatus.PUBLISHED.value, TargetStatus.PUBLISHING.value)
            for t in targets
        )
        if post.status != PostStatus.SCHEDULED.value and not failed:
            raise AppError("meta_post_locked", "errors.meta_post_locked", status_code=409)
        if failed:
            for target in targets:
                if target.status == TargetStatus.FAILED.value:
                    target.status = TargetStatus.PENDING.value
                    target.attempts = 0
                    target.claim_id = None
                    target.claimed_at = None
                    target.container_id = None
                    target.last_error = None
                    target.last_error_code = None
            # The time it failed at is behind us. A draft that still carries it reads as
            # planned, and the next "schedule" would be refused for a time nobody chose.
            post.scheduled_at = None
            return await self._move(post, PostStatus.DRAFT.value, "meta.post_reopened")
        await self.publisher.withdraw(post)
        return await self._move(post, PostStatus.DRAFT.value, "meta.post_unscheduled")

    async def cancel(self, post: MetaPost) -> MetaPost:
        """Withdraw a post for good, keeping it as a record of what was planned."""
        if post.status == PostStatus.SCHEDULED.value:
            self.ctx.require("meta.post.publish")
            await self.publisher.withdraw(post)
        else:
            self.ctx.require("meta.post.write")
            if post.status not in (PostStatus.DRAFT.value, PostStatus.REVIEW.value):
                raise AppError("meta_post_locked", "errors.meta_post_locked", status_code=409)
        for target in await self.publisher.targets(post.id):
            if target.status in (TargetStatus.PENDING.value, TargetStatus.FAILED.value):
                target.status = TargetStatus.CANCELLED.value
        return await self._move(post, PostStatus.CANCELLED.value, "meta.post_cancelled")

    async def retry(self, post: MetaPost) -> MetaPost:
        """Try the deliveries that failed once more, now. The ones that landed are left alone.

        A person presses this after reading why it failed, which is the difference from the
        worker retrying by itself: by now somebody knows the first attempt made nothing.
        """
        self.ctx.require("meta.post.publish")
        if post.status not in (PostStatus.FAILED.value, PostStatus.PARTIAL.value):
            raise AppError("meta_post_locked", "errors.meta_post_locked", status_code=409)
        await self.meta.require_writes_enabled()
        retried = 0
        for target in await self.publisher.targets(post.id):
            if target.status == TargetStatus.FAILED.value:
                target.status = TargetStatus.PENDING.value
                target.scheduler = Scheduler.SCHAKL.value
                target.attempts = 0
                target.container_id = None
                target.claim_id = None
                target.claimed_at = None
                target.last_error = None
                target.last_error_code = None
                retried += 1
        if not retried:
            return post
        post.scheduled_at = datetime.now(UTC)
        post.status = PostStatus.SCHEDULED.value
        post.approved_by_user_id = self._user_id()
        post.approved_by_name = self._user_name()
        post.approved_at = datetime.now(UTC)
        await self.ctx.session.flush()
        await self.activity.record(_ENTITY, post.id, "meta.post_retried", {"deliveries": retried})
        await self._wake_worker(post)
        return post

    async def duplicate(self, post: MetaPost) -> MetaPost:
        """A new draft with the same words and channels — and none of the images.

        The images are files that belong to the original post; copying the *rows* would make
        two posts own one upload, and the first one deleted would take the other's pictures
        with it. Words and channels are the part worth not retyping.
        """
        self.ctx.require("meta.post.write")
        targets = await self.publisher.targets(post.id)
        return await self.create(
            asset_ids=[t.asset_id for t in targets],
            format=post.format,
            body=post.body,
            link=post.link,
            notes=post.notes,
            overrides={t.asset_id: t.body_override for t in targets if t.body_override},
        )

    async def delete(self, post: MetaPost) -> None:
        self.ctx.require("meta.post.write")
        if post.status not in DELETABLE_STATUSES:
            raise AppError("meta_post_locked", "errors.meta_post_published", status_code=409)
        targets = await self.publisher.targets(post.id)
        if any(t.status == TargetStatus.PUBLISHED.value for t in targets):
            raise AppError("meta_post_locked", "errors.meta_post_published", status_code=409)
        await media_lib.revoke_tokens(self.ctx, post.id)
        # The trail line is written before the row goes (§16): it is what is left.
        await self.activity.record(
            _ENTITY,
            post.id,
            "meta.post_deleted",
            {"title": (post.body or "")[:80], "status": post.status},
        )
        await self.ctx.session.delete(post)
        await self.ctx.session.flush()

    # --- internals --------------------------------------------------------------------------- #

    async def _move(self, post: MetaPost, status: str, action: str) -> MetaPost:
        before = post.status
        post.status = status
        if status in (PostStatus.DRAFT.value, PostStatus.CANCELLED.value):
            post.approved_by_user_id = None
            post.approved_by_name = ""
            post.approved_at = None
        await self.ctx.session.flush()
        await self.activity.record(_ENTITY, post.id, action, {"from": before, "to": status})
        return post

    async def _wake_worker(self, post: MetaPost) -> None:
        """Ask the worker to look at this post now rather than at the next tick.

        A nicety: the sweep finds it within the minute regardless, so a queue that is down
        costs a minute and never the post.
        """
        try:
            await enqueue(
                JOB_PUBLISH_POST,
                str(self.ctx.org.id),
                str(post.id),
                _job_id=f"meta-publish-{post.id}-{int(datetime.now(UTC).timestamp())}",
            )
        except Exception:  # noqa: BLE001 — see above
            pass

    async def _destinations(self, asset_ids: list[uuid.UUID]) -> list[MetaAsset]:
        """The assets a post goes to: visible to this caller, and places a post can go."""
        wanted = list(dict.fromkeys(asset_ids))
        if not wanted:
            return []
        rows = (
            await self.ctx.session.scalars(
                self.ctx.repo(MetaAsset).scoped_select().where(MetaAsset.id.in_(wanted))
            )
        ).all()
        by_id = {row.id: row for row in rows}
        out = []
        for asset_id in wanted:
            asset = by_id.get(asset_id)
            if asset is None or asset.kind == KIND_AD_ACCOUNT:
                # Outside the horizon and "not a channel" are one answer, so neither reveals
                # whether the id exists (§15).
                raise _invalid("asset_ids", "errors.meta_asset_unknown")
            out.append(asset)
        return out

    async def _assets(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, MetaAsset]:
        if not ids:
            return {}
        rows = await self.ctx.session.scalars(
            select(MetaAsset).where(MetaAsset.org_id == self.ctx.org.id, MetaAsset.id.in_(ids))
        )
        return {row.id: row for row in rows.all()}

    async def check_assets(self, assets: list[MetaAsset]) -> dict[uuid.UUID, CheckAsset]:
        credential_ids = {a.credential_id for a in assets if a.credential_id is not None}
        credentials: dict[uuid.UUID, MetaCredential] = {}
        if credential_ids:
            rows = await self.ctx.session.scalars(
                select(MetaCredential).where(
                    MetaCredential.org_id == self.ctx.org.id,
                    MetaCredential.id.in_(credential_ids),
                )
            )
            credentials = {row.id: row for row in rows.all()}
        out = {}
        for asset in assets:
            credential = credentials.get(asset.credential_id) if asset.credential_id else None
            caps = capabilities(credential.scopes or []) if credential is not None else {}
            needed = "instagram_publish" if asset.kind == KIND_INSTAGRAM else "facebook_publish"
            out[asset.id] = CheckAsset(
                id=asset.id,
                kind=asset.kind,
                name=asset.name,
                active=asset.active,
                has_credential=credential is not None,
                credential_ok=bool(
                    credential is not None
                    and credential.active
                    and credential.status != CredentialStatus.EXPIRED.value
                ),
                tasks=tuple(asset.tasks or ()),
                linked_page_id=asset.linked_page_id,
                # A token whose scopes were never read (no app configured yet) is not judged
                # on them: "we could not look" must not read as "it is missing".
                can_publish=bool(caps.get(needed, True))
                if credential is not None and credential.scopes
                else True,
            )
        return out

    async def _set_targets(
        self, post: MetaPost, assets: list[MetaAsset], overrides: dict[uuid.UUID, str]
    ) -> None:
        existing = {t.asset_id: t for t in await self.publisher.targets(post.id)}
        wanted = {asset.id for asset in assets}
        for asset_id, target in existing.items():
            if asset_id not in wanted:
                await self.ctx.session.delete(target)
        for asset in assets:
            target = existing.get(asset.id)
            text = (overrides.get(asset.id) or "").strip() or None
            if target is None:
                self.ctx.session.add(
                    MetaPostTarget(
                        org_id=self.ctx.org.id,
                        post_id=post.id,
                        asset_id=asset.id,
                        channel=channel_of(asset.kind) or Channel.FACEBOOK.value,
                        body_override=text,
                    )
                )
            elif asset.id in overrides:
                target.body_override = text
        await self.ctx.session.flush()

    async def _set_overrides(self, post: MetaPost, overrides: dict[uuid.UUID, str]) -> None:
        for target in await self.publisher.targets(post.id):
            if target.asset_id in overrides:
                target.body_override = (overrides[target.asset_id] or "").strip() or None

    async def _instant(self, value: datetime | None) -> datetime | None:
        """A time a caller sent, as the instant it names — a naive one is the org's wall
        clock (§8: the API decides what a clock means)."""
        if value is None:
            return None
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        return as_instant(value, zone).astimezone(UTC)

    def _user_id(self) -> uuid.UUID | None:
        if getattr(self.ctx, "is_system", False):
            return None
        return getattr(self.ctx.user, "id", None)

    def _user_name(self) -> str:
        if getattr(self.ctx, "is_system", False):
            return ""
        user = self.ctx.user
        return str(getattr(user, "full_name", None) or getattr(user, "email", "") or "")


def parse_statuses(raw: str | None) -> set[str] | None:
    """``?status=`` as a set. Absent is everything; ``working`` is the named working set."""
    if not raw or raw.strip() == "all":
        return None
    known = {s.value for s in PostStatus}
    out: set[str] = set()
    for part in raw.split(","):
        token = part.strip()
        if token == "working":
            out.update(WORKING_POST_STATUSES)
        elif token in known:
            out.add(token)
        elif token:
            raise _invalid("status", "errors.validation")
    return out or None


def _one_client(assets: list[MetaAsset]) -> uuid.UUID | None:
    clients = {asset.company_id for asset in assets}
    if len(clients) > 1:
        raise _invalid("asset_ids", "errors.meta_mixed_clients")
    return next(iter(clients), None)


def _format(value: str) -> str:
    if value not in {f.value for f in PostFormat}:
        raise _invalid("format", "errors.validation")
    return value


def _link(value: str | None) -> str | None:
    clean = (value or "").strip()
    if not clean:
        return None
    reject_dangerous_url(clean, field="link")
    if not clean.lower().startswith(("https://", "http://")):
        raise _invalid("link", "errors.invalid_url")
    return clean[:2_000]


def _invalid(field: str, key: str) -> AppError:
    return AppError("validation", "errors.validation", status_code=422, fields={field: key})


def _not_ready(issues: list[Issue]) -> AppError:
    """A refusal that names every reason, by field and in literals (§9, #305)."""
    errors = [issue for issue in issues if issue.level == "error"]
    fields: dict[str, str] = {}
    for issue in errors:
        fields.setdefault(issue.field, issue.code)
    return AppError(
        "meta_post_not_ready",
        "errors.meta_post_not_ready",
        status_code=422,
        fields=fields,
        details={"issues": [issue.as_json() for issue in errors]},
    )


# Re-exported for the router, which must not reach into the publisher for one error type.
__all__ = ["JOB_PUBLISH_POST", "MetaError", "PostService", "PostView", "parse_statuses"]
