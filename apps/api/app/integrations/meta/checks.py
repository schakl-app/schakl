"""What is wrong with a post, per channel, *before* anybody tries to publish it.

A refusal at 09:00 on the day is a post nobody sends. So every rule Meta would apply at
publish time that can be known earlier is applied here, against the draft, and the composer
draws the answer beside the field it is about — the constraint shown working (#305) rather
than discovered.

**Pure on purpose.** It takes the post's fields, its media as measured items and the assets
it goes to, and returns a list. No session, no clock it does not receive, no Meta call: the
composer asks on every save, the scheduler asks before it accepts a time, the publisher asks
before it spends a request, and all three get the same answer.

An ``error`` stops a schedule. A ``warning`` is something the channel will do that the author
may not expect — Instagram dropping the link — and stops nothing.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.metagraph import KIND_INSTAGRAM, KIND_PAGE
from app.integrations.meta.media import (
    FB_BODY_MAX,
    FB_MAX_IMAGE_BYTES,
    IG_CAPTION_MAX,
    IG_HASHTAG_MAX,
    IG_MAX_CAROUSEL,
    IG_MAX_RATIO,
    IG_MIN_RATIO,
    KIND_IMAGE,
    KIND_VIDEO,
    RATIO_TOLERANCE,
    MediaItem,
)
from app.integrations.meta.models import Channel, PostFormat

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

#: Meta's own scheduler accepts a time this far ahead and no nearer…
NATIVE_MIN_LEAD = timedelta(minutes=10)
#: …and no further. Three official pages give three ceilings (30 days, 75 days, 6 months);
#: the strictest is the one that cannot be wrong (docs/META.md §5).
NATIVE_MAX_LEAD = timedelta(days=29)

_HASHTAG = re.compile(r"(?<!\w)#\w+", re.UNICODE)


@dataclass(frozen=True)
class Issue:
    level: str
    #: An i18n key under ``meta.issue.*``.
    code: str
    #: The field the composer draws it beside: ``body``, ``media``, ``link``, ``scheduled_at``,
    #: ``channels``.
    field: str
    #: ``None`` for a problem with the post itself rather than with one channel.
    channel: str | None = None
    asset_id: uuid.UUID | None = None
    #: Literals for the sentence — the limit, the count, the position of the image.
    details: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "field": self.field,
            "channel": self.channel,
            "asset_id": str(self.asset_id) if self.asset_id else None,
            "details": self.details,
        }


@dataclass(frozen=True)
class CheckAsset:
    """What the checks need to know about one destination."""

    id: uuid.UUID
    kind: str
    name: str
    active: bool
    has_credential: bool
    credential_ok: bool
    tasks: tuple[str, ...] = ()
    linked_page_id: str | None = None
    #: Which publishing capabilities the credential's scopes cover.
    can_publish: bool = True

    @property
    def channel(self) -> str:
        return Channel.INSTAGRAM.value if self.kind == KIND_INSTAGRAM else Channel.FACEBOOK.value


def channel_of(kind: str) -> str | None:
    if kind == KIND_PAGE:
        return Channel.FACEBOOK.value
    if kind == KIND_INSTAGRAM:
        return Channel.INSTAGRAM.value
    return None


def check_post(
    *,
    format: str,
    body: str,
    link: str | None,
    media: list[MediaItem],
    assets: list[CheckAsset],
    overrides: dict[uuid.UUID, str] | None = None,
) -> list[Issue]:
    """Every problem with this post as it stands, errors first."""
    issues: list[Issue] = []
    overrides = overrides or {}
    if not assets:
        issues.append(Issue(LEVEL_ERROR, "meta.issue.no_channel", "channels"))
    images = [m for m in media if m.kind == KIND_IMAGE]
    videos = [m for m in media if m.kind == KIND_VIDEO]

    if format == PostFormat.REEL.value:
        if len(videos) != 1 or images:
            issues.append(Issue(LEVEL_ERROR, "meta.issue.reel_needs_one_video", "media"))
    elif len(videos) > 1:
        issues.append(Issue(LEVEL_ERROR, "meta.issue.one_video", "media"))
    elif videos and images:
        # A Page post is photos *or* a video. Instagram would take a mixed carousel, but one
        # post to two channels has to be a post both can carry.
        issues.append(Issue(LEVEL_ERROR, "meta.issue.mixed_media", "media"))

    for asset in assets:
        issues.extend(asset_issues(asset))
        text = overrides.get(asset.id) or body
        if asset.channel == Channel.INSTAGRAM.value:
            issues.extend(_instagram_issues(asset, format, text, link, media))
        else:
            issues.extend(_facebook_issues(asset, format, text, link, media))

    issues.sort(key=lambda issue: 0 if issue.level == LEVEL_ERROR else 1)
    return issues


def schedule_issues(scheduled_at: datetime | None, now: datetime) -> list[Issue]:
    """What is wrong with the *time*. Asked when a schedule is written, never on a draft: a
    draft's time is a proposal, and a proposal for last Tuesday is merely stale."""
    if scheduled_at is None:
        return [Issue(LEVEL_ERROR, "meta.issue.no_time", "scheduled_at")]
    if scheduled_at < now - timedelta(minutes=1):
        return [Issue(LEVEL_ERROR, "meta.issue.time_passed", "scheduled_at")]
    return []


def native_window(scheduled_at: datetime, now: datetime) -> bool:
    """Whether Meta's own scheduler would accept this time right now."""
    lead = scheduled_at - now
    return NATIVE_MIN_LEAD <= lead <= NATIVE_MAX_LEAD


def has_errors(issues: list[Issue]) -> bool:
    return any(issue.level == LEVEL_ERROR for issue in issues)


def asset_issues(asset: CheckAsset) -> list[Issue]:
    common = {"channel": asset.channel, "asset_id": asset.id}
    details = {"name": asset.name}
    if not asset.active:
        return [
            Issue(LEVEL_ERROR, "meta.issue.asset_inactive", "channels", details=details, **common)
        ]
    if not asset.has_credential:
        return [
            Issue(LEVEL_ERROR, "meta.issue.asset_dormant", "channels", details=details, **common)
        ]
    if not asset.credential_ok:
        return [Issue(LEVEL_ERROR, "meta.issue.token_dead", "channels", details=details, **common)]
    out: list[Issue] = []
    if not asset.can_publish:
        out.append(
            Issue(LEVEL_ERROR, "meta.issue.scope_missing", "channels", details=details, **common)
        )
    if asset.kind == KIND_PAGE and asset.tasks and "CREATE_CONTENT" not in asset.tasks:
        # Only where Meta *said* which tasks the token holds. An empty list is "not told",
        # and refusing on something we were never told would block a Page that works.
        out.append(
            Issue(
                LEVEL_ERROR, "meta.issue.page_task_missing", "channels", details=details, **common
            )
        )
    if asset.kind == KIND_INSTAGRAM and not asset.linked_page_id:
        out.append(
            Issue(
                LEVEL_ERROR, "meta.issue.instagram_no_page", "channels", details=details, **common
            )
        )
    return out


def _facebook_issues(
    asset: CheckAsset, format: str, body: str, link: str | None, media: list[MediaItem]
) -> list[Issue]:
    common = {"channel": Channel.FACEBOOK.value, "asset_id": asset.id}
    out: list[Issue] = []
    if not body.strip() and not link and not media:
        out.append(Issue(LEVEL_ERROR, "meta.issue.empty", "body", **common))
    if len(body) > FB_BODY_MAX:
        out.append(
            Issue(
                LEVEL_ERROR,
                "meta.issue.body_too_long",
                "body",
                details={"limit": FB_BODY_MAX, "length": len(body)},
                **common,
            )
        )
    if link and media:
        # A Page post carries a link preview *or* media. With both, the media wins and the
        # link is published as part of the words, which is fine and worth saying.
        out.append(Issue(LEVEL_WARNING, "meta.issue.link_in_text", "link", **common))
    for index, item in enumerate(media):
        if item.kind == KIND_IMAGE and item.size_bytes and item.size_bytes > FB_MAX_IMAGE_BYTES:
            out.append(
                Issue(
                    LEVEL_ERROR,
                    "meta.issue.image_too_large",
                    "media",
                    details={"position": index + 1, "limit_mb": FB_MAX_IMAGE_BYTES // 1_048_576},
                    **common,
                )
            )
    return out


def _instagram_issues(
    asset: CheckAsset, format: str, body: str, link: str | None, media: list[MediaItem]
) -> list[Issue]:
    common = {"channel": Channel.INSTAGRAM.value, "asset_id": asset.id}
    out: list[Issue] = []
    if not media:
        out.append(Issue(LEVEL_ERROR, "meta.issue.instagram_needs_media", "media", **common))
    if len(media) > IG_MAX_CAROUSEL:
        out.append(
            Issue(
                LEVEL_ERROR,
                "meta.issue.too_many_media",
                "media",
                details={"limit": IG_MAX_CAROUSEL, "count": len(media)},
                **common,
            )
        )
    if len(body) > IG_CAPTION_MAX:
        out.append(
            Issue(
                LEVEL_ERROR,
                "meta.issue.body_too_long",
                "body",
                details={"limit": IG_CAPTION_MAX, "length": len(body)},
                **common,
            )
        )
    tags = len(_HASHTAG.findall(body))
    if tags > IG_HASHTAG_MAX:
        out.append(
            Issue(
                LEVEL_ERROR,
                "meta.issue.too_many_hashtags",
                "body",
                details={"limit": IG_HASHTAG_MAX, "count": tags},
                **common,
            )
        )
    if link:
        out.append(Issue(LEVEL_WARNING, "meta.issue.instagram_drops_link", "link", **common))
    if format != PostFormat.REEL.value:
        for index, item in enumerate(media):
            if item.kind != KIND_IMAGE:
                continue
            ratio = item.ratio
            if ratio is None:
                if item.file_id is not None:
                    out.append(
                        Issue(
                            LEVEL_WARNING,
                            "meta.issue.image_unmeasured",
                            "media",
                            details={"position": index + 1},
                            **common,
                        )
                    )
                continue
            if not (IG_MIN_RATIO - RATIO_TOLERANCE <= ratio <= IG_MAX_RATIO + RATIO_TOLERANCE):
                out.append(
                    Issue(
                        LEVEL_ERROR,
                        "meta.issue.image_ratio",
                        "media",
                        details={
                            "position": index + 1,
                            "width": item.width,
                            "height": item.height,
                        },
                        **common,
                    )
                )
    return out
