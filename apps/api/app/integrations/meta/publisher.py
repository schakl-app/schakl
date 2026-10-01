"""Delivering a post to a channel. Business-licensed — see LICENSE.

One class, used by the worker's sweep, by "publish now" and by the tests alike — two code
paths publishing the same post is how a screen and a cron come to disagree about what went out.

**Meta has no idempotency key.** Everything below follows from that one fact:

* **A delivery is claimed in the database before Meta is called.** The claim is a conditional
  ``UPDATE … WHERE status = 'pending'``, committed before the request leaves, so two workers
  and an impatient "publish now" share one winner without sharing any memory
  (docs/PAYMENTS.md's rule, one integration over).
* **A refusal is an answer; silence is not.** Meta saying *no* — a dead token, a rejected
  image — fails the delivery, names why, and leaves it for a person. A call that got **no
  answer** (a timeout, a 5xx) is of *unknown outcome*: the post may be live. It stays
  ``publishing``, and the next sweep **looks** — at the Page's recent posts, at the Instagram
  container — before it does anything else. Only a post that is demonstrably not there is
  tried again, and only twice.
* **What Meta made is written down the moment Meta says so.** An Instagram container's id is
  persisted on a session of its own, mid-flight, because the publish call that follows it is
  exactly the one whose answer may never arrive.

Instagram is always published by this worker at the due time — its API has no scheduling.
Facebook follows the tenant's choice: published here at the due time, or **handed over** to
Meta's own scheduler as soon as the window allows, after which Meta holds the clock and this
class only confirms that it went out.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.core.activity import ActivityService
from app.core.events import emit
from app.core.metagraph import (
    KIND_INSTAGRAM,
    MetaAuthError,
    MetaClient,
    MetaCredentials,
    MetaDuplicate,
    MetaError,
    MetaNotConfigured,
    MetaNotFound,
    MetaRateLimited,
    MetaUnavailable,
    describe_failure,
    meta_client,
)
from app.core.storage.models import StoredFile
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.integrations.meta import media as media_lib
from app.integrations.meta.checks import native_window
from app.integrations.meta.media import KIND_IMAGE, KIND_VIDEO, MediaItem, read_media
from app.integrations.meta.models import (
    Channel,
    MetaAsset,
    MetaPost,
    MetaPostTarget,
    PostFormat,
    PostStatus,
    Scheduler,
    TargetStatus,
)
from app.integrations.meta.service import MetaService

logger = logging.getLogger("schakl.meta")

#: The notification a failed delivery raises. Registered in the notifications module's
#: vocabulary; emitted through the bus, so nothing here imports that module (§6).
EVENT_POST_FAILED = "meta.post_failed"

#: How long a claimed delivery is left alone before the sweep looks at it. Longer than any
#: single request's timeout, so a delivery still genuinely in flight is never second-guessed.
RECHECK_AFTER = timedelta(minutes=2)
#: How many times one delivery is attempted before it is handed to a person.
MAX_ATTEMPTS = 3
#: How long Meta's own scheduler is given, past the due time, before "it has not gone out" is
#: a failure rather than a delay.
NATIVE_GRACE = timedelta(hours=6)
#: How long an Instagram container is waited for inside one delivery. Images finish at once;
#: a video takes minutes, and what is still processing after this is picked up by the sweep.
CONTAINER_POLLS = 8
CONTAINER_POLL_SECONDS = 3.0

#: Our own sentences for ``last_error``, as translatable keys (see ``service.OUR_ERROR_PREFIX``).
ERROR_UNKNOWN_OUTCOME = "meta.error.unknown_outcome"
ERROR_NOT_PUBLISHED_BY_META = "meta.error.not_published_by_meta"
ERROR_CONTAINER_FAILED = "meta.error.container_failed"
ERROR_MEDIA_MISSING = "meta.error.media_missing"
ERROR_WRITES_DISABLED = "meta.error.writes_disabled"


@dataclass
class Outcome:
    """What one attempt came to."""

    #: ``published`` · ``handed_over`` · ``waiting`` (still processing, look again) ·
    #: ``failed`` · ``unknown`` (no answer: look before retrying).
    state: str
    external_id: str | None = None
    permalink: str | None = None
    container_id: str | None = None
    error: str | None = None
    error_code: str | None = None


@dataclass
class _Work:
    """Everything one delivery needs, read before the database is released."""

    target: MetaPostTarget
    post: MetaPost
    asset: MetaAsset
    credentials: MetaCredentials
    media: list[MediaItem]
    files: dict[uuid.UUID, StoredFile]
    #: ``{file id: public address}`` for the media Instagram has to fetch.
    addresses: dict[uuid.UUID, str] = field(default_factory=dict)
    page_token: str | None = None
    #: When set, the delivery is a hand-over to Meta's scheduler for this instant.
    schedule_for: datetime | None = None

    @property
    def text(self) -> str:
        return (self.target.body_override or self.post.body or "").strip()


#: The statuses worth a line on the trail, and the act each is recorded as.
_OUTCOME_ACTIONS = {
    PostStatus.PUBLISHED.value: "meta.post_published",
    PostStatus.PARTIAL.value: "meta.post_partly_published",
    PostStatus.FAILED.value: "meta.post_not_published",
}
#: A channel's name is a brand name: the same word in every language.
_CHANNEL_NAMES = {"facebook": "Facebook", "instagram": "Instagram"}


class Publisher:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.meta = MetaService(ctx)

    # --- the sweep --------------------------------------------------------------------------- #

    async def run_due(
        self, *, now: datetime | None = None, post_id: uuid.UUID | None = None
    ) -> int:
        """Everything in this org that is due for *something*, done. Returns how many
        deliveries were touched.

        Four kinds of row are due: one whose time has come, one Meta's scheduler may now be
        handed, one that was claimed and never answered, and one Meta was handed whose time
        has passed. A failure in one never stops the next.
        """
        now = now or datetime.now(UTC)
        settings_row = await self.meta.settings_row()
        if settings_row is not None and not settings_row.writes_enabled:
            return 0
        stmt = (
            select(MetaPostTarget.id, MetaPostTarget.status, MetaPostTarget.scheduler)
            .join(MetaPost, MetaPost.id == MetaPostTarget.post_id)
            .where(
                MetaPostTarget.org_id == self.ctx.org.id,
                MetaPost.status.in_((PostStatus.SCHEDULED.value, PostStatus.PUBLISHING.value)),
                MetaPost.scheduled_at.isnot(None),
            )
        )
        if post_id is not None:
            stmt = stmt.where(MetaPost.id == post_id)
        due_now = MetaPost.scheduled_at <= now
        stmt = stmt.where(
            (
                (MetaPostTarget.status == TargetStatus.PENDING.value)
                & (
                    due_now
                    | (
                        (MetaPostTarget.scheduler == Scheduler.META.value)
                        & (MetaPost.scheduled_at <= now + timedelta(days=29))
                    )
                )
            )
            | (
                (MetaPostTarget.status == TargetStatus.PUBLISHING.value)
                & (MetaPostTarget.claimed_at < now - RECHECK_AFTER)
            )
            | ((MetaPostTarget.status == TargetStatus.HANDED_OVER.value) & due_now)
        ).order_by(MetaPost.scheduled_at, MetaPostTarget.created_at)
        rows = (await self.ctx.session.execute(stmt)).all()
        touched = 0
        for target_id, status, _scheduler in rows:
            try:
                if status == TargetStatus.PENDING.value:
                    done = await self.deliver(target_id, now=now)
                elif status == TargetStatus.PUBLISHING.value:
                    done = await self.recheck(target_id, now=now)
                else:
                    done = await self.confirm_handed_over(target_id, now=now)
            except Exception:  # noqa: BLE001 — one delivery must never end the sweep
                logger.exception("meta: delivery %s failed unexpectedly", target_id)
                await self.ctx.session.rollback()
                await set_current_org(self.ctx.session, self.ctx.org.id)
                continue
            if done:
                touched += 1
                await self.ctx.session.commit()
                await set_current_org(self.ctx.session, self.ctx.org.id)
        return touched

    # --- one delivery ------------------------------------------------------------------------ #

    async def deliver(self, target_id: uuid.UUID, *, now: datetime | None = None) -> bool:
        """Claim a pending delivery and carry it out. ``False`` when there was nothing to do —
        somebody else holds it, or its time has not come."""
        now = now or datetime.now(UTC)
        target = await self._target(target_id)
        if target is None or target.status != TargetStatus.PENDING.value:
            return False
        post = await self._post(target.post_id)
        if post is None or post.scheduled_at is None:
            return False
        hand_over = (
            target.scheduler == Scheduler.META.value
            and target.channel == Channel.FACEBOOK.value
            and native_window(post.scheduled_at, now)
        )
        if not hand_over and post.scheduled_at > now:
            # Meta's scheduler is wanted and the window is not open (too far ahead), or this
            # is ours to publish and it is not time. Either way: not yet.
            return False
        if not await self._claim(target.id, TargetStatus.PENDING.value, now):
            return False
        await self.ctx.session.refresh(target)
        return await self._attempt(
            target, post, now=now, schedule_for=post.scheduled_at if hand_over else None
        )

    async def recheck(self, target_id: uuid.UUID, *, now: datetime | None = None) -> bool:
        """A delivery that was claimed and never finished: **look first**, then decide."""
        now = now or datetime.now(UTC)
        target = await self._target(target_id)
        if target is None or target.status != TargetStatus.PUBLISHING.value:
            return False
        # Re-claim on the clock, so two sweeps do not both go looking.
        result = await self.ctx.session.execute(
            update(MetaPostTarget)
            .where(
                MetaPostTarget.id == target.id,
                MetaPostTarget.status == TargetStatus.PUBLISHING.value,
                MetaPostTarget.claimed_at < now - RECHECK_AFTER,
            )
            .values(claimed_at=now)
            .returning(MetaPostTarget.id)
        )
        if result.first() is None:
            return False
        await self.ctx.session.refresh(target)
        post = await self._post(target.post_id)
        if post is None:
            return False
        try:
            work = await self._prepare(target, post, schedule_for=None)
        except (MetaError, AppError) as exc:
            await self._finish(target, post, _refused(exc))
            return True

        found: Outcome | None = None
        async with meta_client(work.credentials, tool="recheck") as client, self.ctx.release_db():
            try:
                found = await self._look(client, work)
            except MetaError as exc:
                logger.info("meta: could not look for delivery %s: %s", target.id, exc)
        if found is not None and found.state != "absent":
            await self._finish(target, post, found)
            return True
        if found is None:
            # Could not look. Doing anything else now would be guessing; the next sweep asks
            # again, and the attempts ceiling still bounds how long this can go on.
            if target.attempts >= MAX_ATTEMPTS + 2:
                await self._finish(target, post, Outcome("failed", error=ERROR_UNKNOWN_OUTCOME))
            else:
                target.attempts += 1
            return True
        if target.attempts >= MAX_ATTEMPTS:
            await self._finish(target, post, Outcome("failed", error=ERROR_UNKNOWN_OUTCOME))
            return True
        target.attempts += 1
        await self.ctx.session.flush()
        hand_over = (
            target.scheduler == Scheduler.META.value
            and post.scheduled_at is not None
            and native_window(post.scheduled_at, now)
        )
        return await self._attempt(
            target, post, now=now, schedule_for=post.scheduled_at if hand_over else None
        )

    async def confirm_handed_over(
        self, target_id: uuid.UUID, *, now: datetime | None = None
    ) -> bool:
        """A post Meta's scheduler holds, whose time has passed: did it go out?"""
        now = now or datetime.now(UTC)
        target = await self._target(target_id)
        if target is None or target.status != TargetStatus.HANDED_OVER.value:
            return False
        post = await self._post(target.post_id)
        if post is None or post.scheduled_at is None or not target.external_id:
            return False
        try:
            work = await self._prepare(target, post, schedule_for=None, with_media=False)
        except (MetaError, AppError) as exc:
            target.last_error = _refused(exc).error
            return True
        outcome: Outcome | None = None
        async with meta_client(work.credentials, tool="confirm") as client, self.ctx.release_db():
            try:
                page = await self._as_page(client, work)
                answer = await page.get(
                    target.external_id, {"fields": "id,is_published,permalink_url"}
                )
                if answer.get("is_published"):
                    outcome = Outcome(
                        "published",
                        external_id=target.external_id,
                        permalink=str(answer.get("permalink_url") or "") or None,
                    )
            except MetaNotFound:
                outcome = Outcome("failed", error=ERROR_NOT_PUBLISHED_BY_META)
            except MetaError as exc:
                logger.info("meta: could not confirm %s: %s", target.id, exc)
        if outcome is None and now - post.scheduled_at > NATIVE_GRACE:
            outcome = Outcome("failed", error=ERROR_NOT_PUBLISHED_BY_META)
        if outcome is None:
            return False
        await self._finish(target, post, outcome)
        return True

    async def withdraw(self, post: MetaPost) -> None:
        """Take back what Meta's scheduler was handed, so the post is ours to change again.

        Raises when Meta will not let go: the caller's edit is then refused, with nothing
        changed on either side, rather than saved here and left contradicting the planner
        in Meta Business Suite.
        """
        targets = [
            t
            for t in await self.targets(post.id)
            if t.status == TargetStatus.HANDED_OVER.value and t.external_id
        ]
        for target in targets:
            work = await self._prepare(target, post, schedule_for=None, with_media=False)
            async with (
                meta_client(work.credentials, tool="withdraw") as client,
                self.ctx.release_db(),
            ):
                page = await self._as_page(client, work)
                try:
                    await page.delete(target.external_id)
                except MetaNotFound:
                    pass  # Already gone at Meta's end, which is the state that was asked for.
            target.status = TargetStatus.PENDING.value
            target.external_id = None
            target.permalink = None
            target.claim_id = None
            target.claimed_at = None
            target.last_error = None
            target.last_error_code = None
        await self.ctx.session.flush()

    async def targets(self, post_id: uuid.UUID) -> list[MetaPostTarget]:
        stmt = (
            select(MetaPostTarget)
            .where(MetaPostTarget.org_id == self.ctx.org.id, MetaPostTarget.post_id == post_id)
            .order_by(MetaPostTarget.channel, MetaPostTarget.created_at)
        )
        return list((await self.ctx.session.scalars(stmt)).all())

    # --- internals: the attempt -------------------------------------------------------------- #

    async def _attempt(
        self,
        target: MetaPostTarget,
        post: MetaPost,
        *,
        now: datetime,
        schedule_for: datetime | None,
    ) -> bool:
        if post.status == PostStatus.SCHEDULED.value:
            post.status = PostStatus.PUBLISHING.value
        try:
            work = await self._prepare(target, post, schedule_for=schedule_for)
        except (MetaError, AppError) as exc:
            await self._finish(target, post, _refused(exc))
            return True

        outcome: Outcome
        async with meta_client(work.credentials, tool="publish") as client, self.ctx.release_db():
            try:
                if target.channel == Channel.INSTAGRAM.value:
                    outcome = await self._instagram(client, work)
                else:
                    try:
                        outcome = await self._facebook(client, work)
                    except MetaAuthError:
                        if work.page_token is None:
                            raise
                        # The *cached* Page token was refused, which happens before anything
                        # is made. Ask for a new one and try once more; a second refusal is
                        # the credential's own and is reported as such.
                        work.asset.page_token_encrypted = None
                        work.asset.page_token_at = None
                        work.page_token = None
                        outcome = await self._facebook(client, work)
            except MetaDuplicate:
                # Meta says an identical post was just made. That is our own earlier attempt
                # having landed after all — so go and find it rather than call it a failure.
                outcome = Outcome("unknown", error=ERROR_UNKNOWN_OUTCOME)
            except (MetaUnavailable, MetaRateLimited) as exc:
                # A rate limit is refused *before* anything is made, so it is safe to try
                # again; an outage is not known to be. Both wait for the sweep, which looks.
                outcome = Outcome("unknown", error=describe_failure(exc), error_code=exc.code)
            except MetaError as exc:
                outcome = _refused(exc)
        await self._finish(target, post, outcome)
        return True

    async def _prepare(
        self,
        target: MetaPostTarget,
        post: MetaPost,
        *,
        schedule_for: datetime | None,
        with_media: bool = True,
    ) -> _Work:
        """Read everything a delivery needs. The last thing that touches the session."""
        asset = await self.ctx.session.scalar(
            select(MetaAsset).where(
                MetaAsset.org_id == self.ctx.org.id, MetaAsset.id == target.asset_id
            )
        )
        if asset is None:
            raise MetaNotConfigured("the asset for this delivery no longer exists")
        credentials = await self.meta.credentials_for(asset)
        items = read_media(post.media) if with_media else []
        files: dict[uuid.UUID, StoredFile] = {}
        file_ids = [item.file_id for item in items if item.file_id is not None]
        if file_ids:
            rows = await self.ctx.session.scalars(
                select(StoredFile).where(
                    StoredFile.org_id == self.ctx.org.id, StoredFile.id.in_(file_ids)
                )
            )
            files = {row.id: row for row in rows.all()}
            missing = [fid for fid in file_ids if fid not in files]
            if missing:
                raise AppError("meta_media_missing", ERROR_MEDIA_MISSING, status_code=409)
        work = _Work(
            target=target,
            post=post,
            asset=asset,
            credentials=credentials,
            media=items,
            files=files,
            schedule_for=schedule_for,
        )
        if target.channel == Channel.INSTAGRAM.value and with_media:
            for item in items:
                if item.file_id is not None:
                    token = await media_lib.mint_token(self.ctx, post.id, item.file_id)
                    work.addresses[item.file_id] = media_lib.public_url(
                        self.ctx.org, token, item.kind
                    )
        if asset.kind != KIND_INSTAGRAM:
            work.page_token = self.meta.cached_page_token(asset)
        return work

    async def _as_page(self, client: MetaClient, work: _Work, *, fresh: bool = False) -> MetaClient:
        return await self.meta.page_client(client, work.asset, fresh=fresh)

    # --- internals: Facebook ----------------------------------------------------------------- #

    async def _facebook(self, client: MetaClient, work: _Work) -> Outcome:
        page = await self._as_page(client, work)
        page_id = work.asset.external_id
        timing: dict[str, Any] = {}
        if work.schedule_for is not None:
            timing = {
                "published": False,
                "scheduled_publish_time": int(work.schedule_for.timestamp()),
            }
        images = [m for m in work.media if m.kind == KIND_IMAGE]
        videos = [m for m in work.media if m.kind == KIND_VIDEO]
        link = (work.post.link or "").strip() or None
        text = work.text
        if link and work.media and link not in text:
            text = f"{text}\n\n{link}".strip()

        if work.post.format == PostFormat.REEL.value and videos:
            external = await self._facebook_reel(page, page_id, videos[0], text, work)
        elif videos:
            answer = await page.post(
                f"{page_id}/videos",
                {"file_url": self._address(videos[0], work), "description": text, **timing},
            )
            external = str(answer.get("id") or "")
        elif not images:
            answer = await page.post(f"{page_id}/feed", {"message": text, "link": link, **timing})
            external = str(answer.get("id") or "")
        elif len(images) == 1:
            data: dict[str, Any] = {"caption": text, **timing}
            if images[0].alt:
                data["alt_text_custom"] = images[0].alt
            if work.schedule_for is not None:
                data["unpublished_content_type"] = "SCHEDULED"
            answer = await self._photo(page, page_id, images[0], work, data)
            external = str(answer.get("post_id") or answer.get("id") or "")
        else:
            attached = []
            for item in images:
                data = {"published": False}
                if work.schedule_for is not None:
                    data["temporary"] = True
                if item.alt:
                    data["alt_text_custom"] = item.alt
                answer = await self._photo(page, page_id, item, work, data)
                attached.append({"media_fbid": str(answer.get("id") or "")})
            feed: dict[str, Any] = {"message": text, "attached_media": attached, **timing}
            if work.schedule_for is not None:
                feed["unpublished_content_type"] = "SCHEDULED"
            answer = await page.post(f"{page_id}/feed", feed)
            external = str(answer.get("id") or "")

        if not external:
            return Outcome("unknown", error=ERROR_UNKNOWN_OUTCOME)
        if work.schedule_for is not None:
            return Outcome("handed_over", external_id=external)
        return Outcome(
            "published", external_id=external, permalink=await _permalink(page, external)
        )

    async def _photo(
        self,
        page: MetaClient,
        page_id: str,
        item: MediaItem,
        work: _Work,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """One photo, as bytes where we hold them and as an address where we were given one."""
        if item.file_id is None:
            return await page.post(f"{page_id}/photos", {**data, "url": item.url})
        stored = work.files[item.file_id]
        raw = await media_lib.read_bytes(stored)
        return await page.post(
            f"{page_id}/photos",
            data,
            files={"source": (stored.filename or "image", raw, stored.content_type)},
        )

    async def _facebook_reel(
        self, page: MetaClient, page_id: str, video: MediaItem, text: str, work: _Work
    ) -> str:
        started = await page.post(f"{page_id}/video_reels", {"upload_phase": "start"})
        video_id = str(started.get("video_id") or "")
        upload_url = str(started.get("upload_url") or "")
        if not video_id or not upload_url:
            raise MetaUnavailable("meta did not open an upload for this reel")
        await page.upload_from_url(upload_url, self._address(video, work))
        finish: dict[str, Any] = {
            "upload_phase": "finish",
            "video_id": video_id,
            "description": text,
            "video_state": "PUBLISHED",
        }
        if work.schedule_for is not None:
            finish["video_state"] = "SCHEDULED"
            finish["scheduled_publish_time"] = int(work.schedule_for.timestamp())
        await page.post(f"{page_id}/video_reels", finish)
        return video_id

    @staticmethod
    def _address(item: MediaItem, work: _Work) -> str:
        if item.url:
            return item.url
        address = work.addresses.get(item.file_id) if item.file_id else None
        if not address:
            raise AppError("meta_media_missing", ERROR_MEDIA_MISSING, status_code=409)
        return address

    # --- internals: Instagram ---------------------------------------------------------------- #

    async def _instagram(self, client: MetaClient, work: _Work) -> Outcome:
        account = work.asset.external_id
        container = work.target.container_id
        if not container:
            container = await self._instagram_container(client, account, work)
            # Written down now, on a session of its own: the publish call that follows is the
            # one whose answer may never arrive, and this id is how the sweep asks about it.
            await self._persist(work.target.id, container_id=container)
        return await self._instagram_publish(client, account, container)

    async def _instagram_container(self, client: MetaClient, account: str, work: _Work) -> str:
        caption = work.text
        reel = work.post.format == PostFormat.REEL.value
        items = work.media

        def one(item: MediaItem, *, child: bool) -> dict[str, Any]:
            data: dict[str, Any] = {}
            if item.kind == KIND_VIDEO:
                data["video_url"] = self._address(item, work)
                data["media_type"] = "VIDEO" if child else "REELS"
                if not child:
                    data["share_to_feed"] = True
            else:
                data["image_url"] = self._address(item, work)
                if item.alt and not child:
                    data["alt_text"] = item.alt
            if child:
                data["is_carousel_item"] = True
            else:
                data["caption"] = caption
            return data

        if reel or len(items) == 1:
            answer = await client.post(f"{account}/media", one(items[0], child=False))
            return _required_id(answer)
        children = []
        for item in items:
            answer = await client.post(f"{account}/media", one(item, child=True))
            children.append(_required_id(answer))
        answer = await client.post(
            f"{account}/media",
            {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption},
        )
        return _required_id(answer)

    async def _instagram_publish(self, client: MetaClient, account: str, container: str) -> Outcome:
        status = ""
        for attempt in range(CONTAINER_POLLS):
            answer = await client.get(container, {"fields": "status_code,status"})
            status = str(answer.get("status_code") or "").upper()
            if status in ("FINISHED", "PUBLISHED", "ERROR", "EXPIRED"):
                break
            if attempt + 1 < CONTAINER_POLLS:
                await asyncio.sleep(CONTAINER_POLL_SECONDS)
        if status in ("ERROR", "EXPIRED"):
            return Outcome(
                "failed",
                container_id=container,
                error=ERROR_CONTAINER_FAILED,
                error_code="meta_invalid",
            )
        if status == "PUBLISHED":
            return Outcome("unknown", container_id=container, error=ERROR_UNKNOWN_OUTCOME)
        if status != "FINISHED":
            return Outcome("waiting", container_id=container)
        answer = await client.post(f"{account}/media_publish", {"creation_id": container})
        media_id = _required_id(answer)
        permalink = None
        try:
            detail = await client.get(media_id, {"fields": "permalink"})
            permalink = str(detail.get("permalink") or "") or None
        except MetaError:
            pass  # It is published; the link is a nicety a later read can fill in.
        return Outcome(
            "published", external_id=media_id, permalink=permalink, container_id=container
        )

    # --- internals: looking ------------------------------------------------------------------ #

    async def _look(self, client: MetaClient, work: _Work) -> Outcome:
        """Whether an attempt that got no answer made something after all.

        ``absent`` is a finding, not a failure: we looked, in the place it would be, and it
        is not there.
        """
        since = (work.target.claimed_at or datetime.now(UTC)) - timedelta(minutes=5)
        text = work.text
        if work.target.channel == Channel.INSTAGRAM.value:
            container = work.target.container_id
            if not container:
                return Outcome("absent")
            answer = await client.get(container, {"fields": "status_code"})
            status = str(answer.get("status_code") or "").upper()
            if status == "FINISHED":
                return await self._instagram_publish(client, work.asset.external_id, container)
            if status == "IN_PROGRESS":
                return Outcome("waiting", container_id=container)
            if status in ("ERROR", "EXPIRED"):
                return Outcome("failed", error=ERROR_CONTAINER_FAILED, error_code="meta_invalid")
            recent = await client.get(
                f"{work.asset.external_id}/media",
                {"fields": "id,caption,permalink,timestamp", "limit": 10},
            )
            for row in recent.get("data") or ():
                if _same_text(row.get("caption"), text) and _after(row.get("timestamp"), since):
                    return Outcome(
                        "published",
                        external_id=str(row.get("id") or ""),
                        permalink=str(row.get("permalink") or "") or None,
                    )
            # The container says published and the feed does not show it yet. It *is*
            # published; only the link is missing.
            return Outcome("published", external_id=None, container_id=container)

        page = await self._as_page(client, work)
        page_id = work.asset.external_id
        recent = await page.get(
            f"{page_id}/feed",
            {"fields": "id,message,created_time,permalink_url", "limit": 25},
        )
        for row in recent.get("data") or ():
            if _matches(row.get("message"), text, work) and _after(row.get("created_time"), since):
                return Outcome(
                    "published",
                    external_id=str(row.get("id") or ""),
                    permalink=str(row.get("permalink_url") or "") or None,
                )
        if work.target.scheduler == Scheduler.META.value:
            planned = await page.get(
                f"{page_id}/scheduled_posts",
                {"fields": "id,message,scheduled_publish_time", "limit": 50},
            )
            for row in planned.get("data") or ():
                if _matches(row.get("message"), text, work):
                    return Outcome("handed_over", external_id=str(row.get("id") or ""))
        return Outcome("absent")

    # --- internals: bookkeeping -------------------------------------------------------------- #

    async def _finish(self, target: MetaPostTarget, post: MetaPost, outcome: Outcome) -> None:
        now = datetime.now(UTC)
        if outcome.container_id:
            target.container_id = outcome.container_id
        if outcome.state == "published":
            target.status = TargetStatus.PUBLISHED.value
            target.external_id = outcome.external_id or target.external_id
            target.permalink = outcome.permalink or target.permalink
            target.published_at = now
            target.last_error = None
            target.last_error_code = None
        elif outcome.state == "handed_over":
            target.status = TargetStatus.HANDED_OVER.value
            target.external_id = outcome.external_id
            target.last_error = None
            target.last_error_code = None
        elif outcome.state == "failed":
            target.status = TargetStatus.FAILED.value
            target.last_error = (outcome.error or "")[:500] or None
            target.last_error_code = outcome.error_code
        else:
            # ``waiting`` and ``unknown`` both stay claimed. The clock on the claim is what
            # brings the sweep back; the error says what is being waited for.
            target.status = TargetStatus.PUBLISHING.value
            target.claimed_at = now
            if outcome.state == "unknown":
                target.last_error = (outcome.error or ERROR_UNKNOWN_OUTCOME)[:500]
                target.last_error_code = outcome.error_code
        await self.ctx.session.flush()
        before = post.status
        await self.settle(post)
        if post.status != before and post.status in (
            PostStatus.FAILED.value,
            PostStatus.PARTIAL.value,
        ):
            await self._notify_failed(post, target)
        if post.status in (
            PostStatus.PUBLISHED.value,
            PostStatus.FAILED.value,
            PostStatus.PARTIAL.value,
        ):
            # The delivery is over; the addresses minted for it stop working, now.
            await media_lib.revoke_tokens(self.ctx, post.id)

    async def settle(self, post: MetaPost) -> None:
        """A post's own status, from what became of its deliveries."""
        if post.status not in (
            PostStatus.SCHEDULED.value,
            PostStatus.PUBLISHING.value,
            PostStatus.PARTIAL.value,
            PostStatus.FAILED.value,
            PostStatus.PUBLISHED.value,
        ):
            return
        targets = [
            t for t in await self.targets(post.id) if t.status != TargetStatus.CANCELLED.value
        ]
        if not targets:
            return
        before = post.status
        states = {t.status for t in targets}
        published = [t for t in targets if t.status == TargetStatus.PUBLISHED.value]
        waiting = {TargetStatus.PENDING.value, TargetStatus.HANDED_OVER.value}
        if TargetStatus.PUBLISHING.value in states:
            post.status = PostStatus.PUBLISHING.value
        elif states <= waiting:
            post.status = PostStatus.SCHEDULED.value
        elif states == {TargetStatus.PUBLISHED.value}:
            post.status = PostStatus.PUBLISHED.value
        elif states & waiting:
            post.status = PostStatus.PUBLISHING.value
        elif published:
            post.status = PostStatus.PARTIAL.value
        else:
            post.status = PostStatus.FAILED.value
        if published:
            post.published_at = max(t.published_at for t in published if t.published_at)
        await self.ctx.session.flush()
        await self._record_outcome(post, before, targets)

    async def _record_outcome(
        self, post: MetaPost, before: str, targets: list[MetaPostTarget]
    ) -> None:
        """What became of the post, on its own trail — once, when it became that.

        The approval is a person's line and the outcome is the system's. Without the second,
        the trail of a post that failed at nine reads exactly like the trail of one that went
        out: "approved and planned", and then nothing.
        """
        action = _OUTCOME_ACTIONS.get(post.status)
        if action is None or post.status == before:
            return
        landed = [t.channel for t in targets if t.status == TargetStatus.PUBLISHED.value]
        missed = [t.channel for t in targets if t.status == TargetStatus.FAILED.value]
        await ActivityService(self.ctx).record(
            "meta_post",
            post.id,
            action,
            {
                "channels": ", ".join(_CHANNEL_NAMES.get(c, c) for c in dict.fromkeys(landed)),
                "failed": ", ".join(_CHANNEL_NAMES.get(c, c) for c in dict.fromkeys(missed)),
            },
        )

    async def _notify_failed(self, post: MetaPost, target: MetaPostTarget) -> None:
        """The people behind the post are told it did not go out. Silence is the failure that
        costs most: a campaign that never started looks, from the inside, like a quiet week."""
        recipients = [
            user_id
            for user_id in dict.fromkeys((post.approved_by_user_id, post.created_by_user_id))
            if user_id is not None
        ]
        if not recipients:
            return
        asset = await self.ctx.session.scalar(
            select(MetaAsset.name).where(
                MetaAsset.org_id == self.ctx.org.id, MetaAsset.id == target.asset_id
            )
        )
        try:
            await emit(
                EVENT_POST_FAILED,
                self.ctx,
                {
                    "meta_post_id": post.id,
                    "title": post_title(post),
                    "channel": target.channel,
                    "asset": asset or "",
                    "_recipients": recipients,
                    "_dedup_key": f"meta-post-failed:{post.id}:{target.id}:{target.attempts}",
                },
            )
        except Exception:  # noqa: BLE001 — a notification is never the delivery's failure
            logger.exception("meta: could not notify about post %s", post.id)

    async def _claim(self, target_id: uuid.UUID, expected: str, now: datetime) -> bool:
        result = await self.ctx.session.execute(
            update(MetaPostTarget)
            .where(
                MetaPostTarget.org_id == self.ctx.org.id,
                MetaPostTarget.id == target_id,
                MetaPostTarget.status == expected,
            )
            .values(
                status=TargetStatus.PUBLISHING.value,
                claim_id=uuid.uuid4(),
                claimed_at=now,
                attempts=MetaPostTarget.attempts + 1,
            )
            .returning(MetaPostTarget.id)
        )
        return result.first() is not None

    async def _persist(self, target_id: uuid.UUID, **values: Any) -> None:
        """Write to one delivery on a session of its own, while the request's is released."""
        async with async_session_maker() as session:
            await set_current_org(session, self.ctx.org.id)
            await session.execute(
                update(MetaPostTarget)
                .where(MetaPostTarget.org_id == self.ctx.org.id, MetaPostTarget.id == target_id)
                .values(**values)
            )
            await session.commit()

    async def _target(self, target_id: uuid.UUID) -> MetaPostTarget | None:
        return await self.ctx.session.scalar(
            select(MetaPostTarget).where(
                MetaPostTarget.org_id == self.ctx.org.id, MetaPostTarget.id == target_id
            )
        )

    async def _post(self, post_id: uuid.UUID) -> MetaPost | None:
        return await self.ctx.session.scalar(
            select(MetaPost).where(MetaPost.org_id == self.ctx.org.id, MetaPost.id == post_id)
        )


def post_title(post: MetaPost) -> str:
    """What a post is called where there is room for one line: its first line, cut."""
    first = next((line.strip() for line in (post.body or "").splitlines() if line.strip()), "")
    if len(first) > 80:
        first = first[:77].rstrip() + "…"
    return first


def _refused(exc: Exception) -> Outcome:
    if isinstance(exc, MetaError):
        return Outcome("failed", error=describe_failure(exc), error_code=exc.code)
    if isinstance(exc, AppError):
        return Outcome("failed", error=exc.message_key, error_code=exc.code)
    return Outcome("failed", error=str(exc)[:500])


def _required_id(answer: dict[str, Any]) -> str:
    value = str(answer.get("id") or "")
    if not value:
        raise MetaUnavailable("meta answered without an id")
    return value


async def _permalink(page: MetaClient, external_id: str) -> str | None:
    try:
        answer = await page.get(external_id, {"fields": "permalink_url"})
    except MetaError:
        return None
    return str(answer.get("permalink_url") or "") or None


def _same_text(found: Any, wanted: str) -> bool:
    return " ".join(str(found or "").split()) == " ".join(wanted.split())


def _matches(found: Any, wanted: str, work: _Work) -> bool:
    """A Page post's words against ours — allowing for the link we append when media and a
    link travel together."""
    if _same_text(found, wanted):
        return True
    link = (work.post.link or "").strip()
    return bool(link) and _same_text(found, f"{wanted}\n\n{link}")


def _after(stamp: Any, since: datetime) -> bool:
    raw = str(stamp or "")
    if not raw:
        return False
    try:
        # Meta writes ``2026-09-29T10:00:00+0000`` — an offset without its colon.
        if len(raw) >= 5 and raw[-5] in "+-" and raw[-3] != ":":
            raw = f"{raw[:-2]}:{raw[-2:]}"
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when >= since
