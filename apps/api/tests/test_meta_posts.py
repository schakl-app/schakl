"""meta: planned posts, from a draft to a channel — through the fake transport.

The worker is driven directly (``run_worker``), exactly as its cron tick drives it, so what is
asserted is the path a real post takes: the claim, the call, the row.

The tests that matter most are the ones about **a call that got no answer**. Meta has no
idempotency key, so a retry after a timeout is how a client ends up with the same post twice;
``FakeMeta.lose_next`` makes the post happen and the answer vanish, which is the only way to
show that the sweep looks before it tries again.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.auth.models import User
from app.core.metagraph import set_transport
from app.db import async_session_maker, set_current_org
from app.integrations.meta.models import MetaMediaToken, MetaPostTarget
from tests.conftest import add_membership, auth_cookie, make_tenant
from tests.meta_fake import IG_CLIENT, PAGE_CLIENT, FakeMeta, error
from tests.meta_helpers import BASE, company, image, member, ready, run_worker, upload

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake() -> FakeMeta:
    stub = FakeMeta()
    set_transport(stub.transport())
    try:
        yield stub
    finally:
        set_transport(None)


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path) -> None:
    """A post's pictures are stored files. The default root is ``/data/storage`` — the
    container's volume, which a CI runner may not create — so every test here writes to its
    own temp directory, as the files tests do."""
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_queue(monkeypatch) -> None:
    """ "Publish now" wakes the worker through the queue; the tests *are* the worker."""
    from app.integrations.meta import posts

    async def _enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(posts, "enqueue", _enqueue)


async def _draft(c, r, *, assets=None, **body) -> dict:  # noqa: ANN001
    payload = {"asset_ids": assets or [r.page_id], "body": "Nieuwe collectie is binnen!"}
    payload.update(body)
    res = await c.post(f"{BASE}/posts", json=payload, headers=r.headers)
    assert res.status_code == 201, res.text
    return res.json()


async def _with_image(c, r, post: dict, **kwargs) -> dict:  # noqa: ANN001
    file_id = await upload(c, r.headers, post["id"], **kwargs)
    res = await c.patch(
        f"{BASE}/posts/{post['id']}",
        json={"media": [{"file_id": file_id, "alt": "Een rode fiets"}]},
        headers=r.headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _rewind(tenant, post_id: str, minutes: int = 5) -> None:  # noqa: ANN001
    """Make the claims of a post look ``minutes`` old, so the sweep may look at them."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        await session.execute(
            update(MetaPostTarget)
            .where(MetaPostTarget.post_id == post_id)
            .values(claimed_at=datetime.now(UTC) - timedelta(minutes=minutes))
        )
        await session.commit()


def _in(minutes: int = 0, days: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes, days=days)).isoformat()


# --- drafting ------------------------------------------------------------------------------- #


async def test_a_draft_reaches_nobody(client_for, fake) -> None:
    r = await ready(client_for, "post-draft")
    before = len(fake.writes())
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, scheduled_at=_in(days=2))
        await run_worker(r.tenant)
    assert post["status"] == "draft"
    assert post["company_id"] == r.company_id
    assert post["company_name"] == "Nova Fietsen"
    assert post["title"] == "Nieuwe collectie is binnen!"
    assert post["targets"][0]["channel"] == "facebook"
    assert post["targets"][0]["asset_name"] == "Nova Fietsen"
    assert post["ready"] is True
    assert len(fake.writes()) == before


async def test_a_naive_time_is_the_orgs_own_clock(client_for, fake) -> None:
    """The API decides what a clock means (§8): what a form sends is the org's wall clock."""
    r = await ready(client_for, "post-clock")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, scheduled_at="2027-01-15T09:00:00")
    # Europe/Amsterdam in January is UTC+1.
    assert post["scheduled_at"].startswith("2027-01-15T08:00:00")


async def test_a_post_is_for_one_client(client_for, fake) -> None:
    """Two clients' Pages on one post is a post with two answers to "whose is this"."""
    r = await ready(client_for, "post-oneclient")
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{BASE}/posts",
            json={"asset_ids": [r.page_id, r.own_page_id], "body": "x"},
            headers=r.headers,
        )
    assert res.status_code == 422
    assert res.json()["error"]["fields"] == {"asset_ids": "errors.meta_mixed_clients"}


async def test_an_ad_account_is_not_a_channel(client_for, fake) -> None:
    r = await ready(client_for, "post-notachannel")
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{BASE}/posts", json={"asset_ids": [r.ad_account_id], "body": "x"}, headers=r.headers
        )
    assert res.status_code == 422
    assert res.json()["error"]["fields"] == {"asset_ids": "errors.meta_asset_unknown"}


async def test_instagram_says_what_it_needs_before_anybody_schedules(client_for, fake) -> None:
    """A refusal at nine on the day is a post nobody sends, so the draft says it now."""
    r = await ready(client_for, "post-igneeds")
    async with client_for(r.tenant.host) as c:
        post = await _draft(
            c, r, assets=[r.page_id, r.instagram_id], link="https://novafietsen.example/nieuw"
        )
        codes = {(i["channel"], i["code"], i["level"]) for i in post["issues"]}
        assert ("instagram", "meta.issue.instagram_needs_media", "error") in codes
        assert ("instagram", "meta.issue.instagram_drops_link", "warning") in codes
        assert post["ready"] is False

        # 1080×400 is wider than Instagram's 1.91:1.
        post = await _with_image(c, r, post, data=image(1080, 400))
        issue = next(i for i in post["issues"] if i["code"] == "meta.issue.image_ratio")
        assert issue["details"] == {"position": 1, "width": 1080, "height": 400}
        assert post["media"][0]["width"] == 1080
        assert post["ready"] is False

        post = await _with_image(c, r, post, data=image(1080, 1350))
        assert post["ready"] is True


async def test_a_caption_over_instagrams_limit_names_the_limit(client_for, fake) -> None:
    r = await ready(client_for, "post-limit")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, assets=[r.instagram_id], body="a" * 2_300)
    issue = next(i for i in post["issues"] if i["code"] == "meta.issue.body_too_long")
    assert issue["details"] == {"limit": 2_200, "length": 2_300}
    assert issue["field"] == "body"


async def test_a_file_that_is_not_this_posts_cannot_be_published_by_it(client_for, fake) -> None:
    """A caller must not make a post publish somebody else's attachment by naming its id."""
    r = await ready(client_for, "post-foreignfile")
    async with client_for(r.tenant.host) as c:
        first = await _draft(c, r)
        second = await _draft(c, r)
        file_id = await upload(c, r.headers, first["id"])
        res = await c.patch(
            f"{BASE}/posts/{second['id']}",
            json={"media": [{"file_id": file_id}]},
            headers=r.headers,
        )
    assert res.status_code == 422
    assert res.json()["error"]["fields"] == {"media.0.file_id": "errors.meta_media_not_found"}


async def test_a_form_s_line_breaks_are_stored_as_line_breaks(client_for, fake) -> None:
    """A browser submits CRLF. Counted and published as sent, a caption two characters under
    Instagram's limit is refused, and Meta prints the carriage returns."""
    r = await ready(client_for, "posts-newlines")
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{BASE}/posts",
            json={
                "asset_ids": [r.page_id],
                "body": "Een\r\n\r\nTwee",
                "overrides": {r.page_id: "Drie\r\nVier"},
            },
            headers=r.headers,
        )
        assert res.status_code == 201, res.text
        post = res.json()
        assert post["body"] == "Een\n\nTwee"
        assert post["targets"][0]["body_override"] == "Drie\nVier"
        res = await c.patch(
            f"{BASE}/posts/{post['id']}", json={"body": "Vijf\r\nZes"}, headers=r.headers
        )
    assert res.json()["body"] == "Vijf\nZes"


async def test_a_post_meta_refused_can_be_taken_back_and_changed(client_for, fake) -> None:
    """A refusal has to be fixable. Without this the ways on from "Meta turned the words
    down" were sending the same words again, or retyping them into a copy."""
    r = await ready(client_for, "posts-rework")
    async with client_for(r.tenant.host) as c:
        post = (
            await c.post(
                f"{BASE}/posts",
                json={"asset_ids": [r.page_id], "body": "Afgekeurd"},
                headers=r.headers,
            )
        ).json()
        fake.fail_next("POST", "feed", error(368, "disallowed"))
        await c.post(f"{BASE}/posts/{post['id']}/publish", json={}, headers=r.headers)
        await run_worker(r.tenant)
        failed = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
        assert failed["status"] == "failed"

        res = await c.post(f"{BASE}/posts/{post['id']}/unschedule", headers=r.headers)
        assert res.status_code == 200, res.text
        draft = res.json()
        assert draft["status"] == "draft"
        assert [t["status"] for t in draft["targets"]] == ["pending"]
        assert draft["targets"][0]["last_error"] is None
        assert draft["scheduled_at"] is None

        res = await c.patch(
            f"{BASE}/posts/{post['id']}", json={"body": "Nu wel goed"}, headers=r.headers
        )
        assert res.status_code == 200, res.text
        await c.post(f"{BASE}/posts/{post['id']}/publish", json={}, headers=r.headers)
        await run_worker(r.tenant)
        done = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert done["status"] == "published"
    assert [p["message"] for p in fake.posts.values()] == ["Nu wel goed"]


async def test_a_post_that_landed_somewhere_is_never_taken_back(client_for, fake) -> None:
    r = await ready(client_for, "posts-rework-partial")
    async with client_for(r.tenant.host) as c:
        post = (
            await c.post(
                f"{BASE}/posts",
                json={"asset_ids": [r.page_id], "body": "Staat live"},
                headers=r.headers,
            )
        ).json()
        await c.post(f"{BASE}/posts/{post['id']}/publish", json={}, headers=r.headers)
        await run_worker(r.tenant)
        res = await c.post(f"{BASE}/posts/{post['id']}/unschedule", headers=r.headers)
    assert res.status_code == 409


async def test_an_agent_can_attach_a_picture_as_json(client_for, fake) -> None:
    """A tool call is a JSON document. Without this an agent could write every part of an
    Instagram post except the one without which nothing is published."""
    r = await ready(client_for, "posts-image-json")
    encoded = base64.b64encode(image(1080, 1080, "JPEG")).decode()
    async with client_for(r.tenant.host) as c:
        post = (
            await c.post(
                f"{BASE}/posts",
                json={"asset_ids": [r.instagram_id], "body": "Met beeld"},
                headers=r.headers,
            )
        ).json()
        assert any(i["code"] == "meta.issue.instagram_needs_media" for i in post["issues"])
        res = await c.post(
            f"{BASE}/posts/{post['id']}/images",
            json={
                "filename": "fiets.jpg",
                "content_type": "image/jpeg",
                "data": f"data:image/jpeg;base64,{encoded}",
                "alt": "Een fiets",
            },
            headers=r.headers,
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert [(m["kind"], m["alt"], m["width"]) for m in body["media"]] == [
            ("image", "Een fiets", 1080)
        ]
        assert not [i for i in body["issues"] if i["level"] == "error"]

        refused = await c.post(
            f"{BASE}/posts/{post['id']}/images",
            json={"filename": "x.gif", "content_type": "image/gif", "data": encoded},
            headers=r.headers,
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["fields"] == {"content_type": "errors.meta_media_type"}
        broken = await c.post(
            f"{BASE}/posts/{post['id']}/images",
            json={"filename": "x.jpg", "content_type": "image/jpeg", "data": "not base64 !!"},
            headers=r.headers,
        )
        assert broken.status_code == 422


async def test_the_client_hub_shows_the_channels_and_what_goes_out_next(client_for, fake) -> None:
    """Soonest first among what is still going on, then what went out, and never a
    cancelled one. Another client's page shows none of it."""
    r = await ready(client_for, "posts-hub")
    other = await company(r.tenant.org.id, "Bakkerij Van Loon")
    async with client_for(r.tenant.host) as c:
        for body, when in (("Later", _in(days=5)), ("Eerst", _in(days=1))):
            post = (
                await c.post(
                    f"{BASE}/posts",
                    json={"asset_ids": [r.page_id], "body": body},
                    headers=r.headers,
                )
            ).json()
            await c.post(
                f"{BASE}/posts/{post['id']}/schedule",
                json={"scheduled_at": when},
                headers=r.headers,
            )
        gone = (
            await c.post(
                f"{BASE}/posts",
                json={"asset_ids": [r.page_id], "body": "Toch niet"},
                headers=r.headers,
            )
        ).json()
        await c.post(f"{BASE}/posts/{gone['id']}/cancel", headers=r.headers)

        hub = await c.get(f"/api/v1/companies/{r.company_id}/panels", headers=r.headers)
        assert hub.status_code == 200, hub.text
        panel = next(p for p in hub.json() if p["key"] == "meta.company")
        assert panel["empty"] is False
        assert {ch["kind"] for ch in panel["data"]["channels"]} == {"page", "instagram"}
        assert [p["title"] for p in panel["data"]["items"]] == ["Eerst", "Later"]
        assert panel["data"]["items"][0]["channels"] == ["facebook"]

        elsewhere = await c.get(f"/api/v1/companies/{other}/panels", headers=r.headers)
        panel = next(p for p in elsewhere.json() if p["key"] == "meta.company")
    assert panel["empty"] is True
    assert panel["data"]["items"] == []


# --- who may do what ------------------------------------------------------------------------ #


async def test_a_member_drafts_and_offers_and_may_not_schedule(client_for, fake) -> None:
    """The split an agency hands out separately: a key that may draft and never broadcast."""
    r = await ready(client_for, "post-member")
    writer = await member(r.tenant, "schrijver@post-member.example.com")
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{BASE}/posts",
            json={"asset_ids": [r.page_id], "body": "Concept", "scheduled_at": _in(days=1)},
            headers=writer,
        )
        assert res.status_code == 201
        post = res.json()
        res = await c.post(f"{BASE}/posts/{post['id']}/offer", headers=writer)
        assert res.json()["status"] == "review"
        for verb in ("schedule", "publish", "unschedule", "retry"):
            res = await c.post(f"{BASE}/posts/{post['id']}/{verb}", json={}, headers=writer)
            assert res.status_code == 403, verb

        res = await c.post(f"{BASE}/posts/{post['id']}/schedule", json={}, headers=r.headers)
        assert res.status_code == 200
        scheduled = res.json()
        assert scheduled["status"] == "scheduled"
        assert scheduled["approved_by_name"] == r.tenant.user.email
        assert scheduled["created_by_name"] == "schrijver@post-member.example.com"

        # A scheduled post is changed by whoever may schedule one: the change is what will
        # be published.
        res = await c.patch(f"{BASE}/posts/{post['id']}", json={"body": "Anders"}, headers=writer)
        assert res.status_code == 403


async def test_scheduling_refuses_a_post_that_cannot_be_published(client_for, fake) -> None:
    r = await ready(client_for, "post-notready")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, assets=[r.instagram_id])
        res = await c.post(
            f"{BASE}/posts/{post['id']}/schedule",
            json={"scheduled_at": _in(days=1)},
            headers=r.headers,
        )
    assert res.status_code == 422
    body = res.json()["error"]
    assert body["code"] == "meta_post_not_ready"
    assert body["fields"] == {"media": "meta.issue.instagram_needs_media"}
    assert body["details"]["issues"][0]["channel"] == "instagram"


async def test_a_time_that_has_passed_is_refused(client_for, fake) -> None:
    r = await ready(client_for, "post-past")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        res = await c.post(
            f"{BASE}/posts/{post['id']}/schedule",
            json={"scheduled_at": _in(minutes=-30)},
            headers=r.headers,
        )
        assert res.status_code == 422
        assert res.json()["error"]["fields"] == {"scheduled_at": "meta.issue.time_passed"}
        res = await c.post(f"{BASE}/posts/{post['id']}/schedule", json={}, headers=r.headers)
        assert res.json()["error"]["fields"] == {"scheduled_at": "meta.issue.no_time"}


async def test_the_kill_switch_stops_scheduling_and_the_worker(client_for, fake) -> None:
    r = await ready(client_for, "post-killswitch")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        res = await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        assert res.status_code == 200
        await c.put(f"{BASE}/settings", json={"writes_enabled": False}, headers=r.headers)
        assert await run_worker(r.tenant) == 0
        assert not fake.writes("/feed")
        other = await _draft(c, r, body="Tweede")
        res = await c.post(f"{BASE}/posts/{other['id']}/publish", headers=r.headers)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "meta_writes_disabled"


# --- publishing ----------------------------------------------------------------------------- #


async def test_a_post_is_published_at_its_time_and_not_before(client_for, fake) -> None:
    r = await ready(client_for, "post-time")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        await c.post(
            f"{BASE}/posts/{post['id']}/schedule",
            json={"scheduled_at": _in(minutes=30)},
            headers=r.headers,
        )
        assert await run_worker(r.tenant) == 0
        assert not fake.writes("/feed")

        assert await run_worker(r.tenant, now=datetime.now(UTC) + timedelta(minutes=31)) == 1
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    assert post["published_at"] is not None
    target = post["targets"][0]
    assert target["status"] == "published"
    assert target["meta_post_id"].startswith(PAGE_CLIENT)
    assert target["permalink"].startswith("https://www.facebook.com/")
    write = fake.writes("/feed")[0]
    assert write.data["message"] == "Nieuwe collectie is binnen!"
    # Published as the Page, never as the system user.
    assert write.token == fake.page_token(PAGE_CLIENT)


async def test_a_facebook_photo_is_sent_as_bytes(client_for, fake) -> None:
    """Facebook takes the image in the request, so a Facebook delivery needs nothing from the
    outside world and works on an instance no stranger can reach."""
    r = await ready(client_for, "post-photo")
    async with client_for(r.tenant.host) as c:
        post = await _with_image(c, r, await _draft(c, r))
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    photo = fake.writes("/photos")[0]
    assert photo.files == ["source"]
    assert photo.data["caption"] == "Nieuwe collectie is binnen!"
    assert photo.data["alt_text_custom"] == "Een rode fiets"
    assert fake.fetched == []


async def test_several_photos_are_one_post(client_for, fake) -> None:
    r = await ready(client_for, "post-album")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        files = [
            await upload(c, r.headers, post["id"], data=image(800 + n, 800), filename=f"{n}.png")
            for n in range(3)
        ]
        await c.patch(
            f"{BASE}/posts/{post['id']}",
            json={"media": [{"file_id": f} for f in files]},
            headers=r.headers,
        )
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    assert len(fake.writes("/photos")) == 3
    assert all(w.data["published"] == "false" for w in fake.writes("/photos"))
    assert len(fake.writes("/feed")) == 1
    assert len(fake.posts) == 1
    assert len(next(iter(fake.posts.values()))["photos"]) == 3


async def test_a_link_and_a_photo_travel_together_in_the_words(client_for, fake) -> None:
    r = await ready(client_for, "post-linkphoto")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, link="https://novafietsen.example/nieuw")
        post = await _with_image(c, r, post)
        assert any(i["code"] == "meta.issue.link_in_text" for i in post["issues"])
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
    caption = fake.writes("/photos")[0].data["caption"]
    assert caption.endswith("https://novafietsen.example/nieuw")


async def test_instagram_is_handed_an_address_that_stops_working_afterwards(
    client_for, fake
) -> None:
    """Instagram fetches media itself, from a public URL. So a delivery mints a capability for
    one file — and withdraws it when the delivery is over."""
    r = await ready(client_for, "post-ig")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, assets=[r.instagram_id], body="Op Instagram")
        post = await _with_image(c, r, post)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()

        assert post["status"] == "published"
        target = post["targets"][0]
        assert target["channel"] == "instagram"
        assert target["permalink"].startswith("https://www.instagram.com/p/")
        container = next(w for w in fake.writes(f"{IG_CLIENT}/media") if "image_url" in w.data)
        assert container.data["caption"] == "Op Instagram"
        assert container.data["alt_text"] == "Een rode fiets"
        address = container.data["image_url"]
        assert address.startswith("https://post-ig.localhost/api/v1/meta-business/media/")
        assert address.endswith(".jpg")

        # Withdrawn: the address Meta was given is dead, to anybody.
        path = address.split("post-ig.localhost", 1)[1]
        assert (await c.get(path)).status_code == 404
    async with async_session_maker() as session:
        await set_current_org(session, r.tenant.org.id)
        tokens = (await session.scalars(select(MetaMediaToken))).all()
    assert tokens and all(token.revoked_at is not None for token in tokens)


async def test_the_public_address_serves_a_jpeg_without_a_session(client_for, fake) -> None:
    """Instagram takes JPEG and nothing else, so that is what the address serves whatever was
    uploaded — and it never sends a referrer or invites a crawler."""
    r = await ready(client_for, "post-media")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, assets=[r.instagram_id])
        post = await _with_image(c, r, post, data=image(1080, 1080, "PNG"))
        # Hold the delivery mid-flight so the address is still alive when it is fetched.
        fake.container_status = "IN_PROGRESS"
        from app.integrations.meta import publisher

        publisher.CONTAINER_POLLS = 1
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        address = next(w for w in fake.writes("/media") if "image_url" in w.data).data["image_url"]
        path = address.split("post-media.localhost", 1)[1]

        res = await c.get(path)
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert res.content[:3] == b"\xff\xd8\xff"
        assert res.headers["referrer-policy"] == "no-referrer"
        assert "noindex" in res.headers["x-robots-tag"]
        assert res.headers["cache-control"] == "no-store"

        assert (await c.get(path.replace(".jpg", "x.jpg"))).status_code == 404
    other = await make_tenant("post-media-other")
    async with client_for(other.host) as c:
        # The org comes from the hostname: another tenant's host knows nothing of this token.
        assert (await c.get(path)).status_code == 404


async def test_a_video_that_is_still_processing_is_picked_up_by_the_next_sweep(
    client_for, fake
) -> None:
    r = await ready(client_for, "post-processing")
    from app.integrations.meta import publisher

    publisher.CONTAINER_POLLS = 1
    fake.container_status = "IN_PROGRESS"
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, assets=[r.instagram_id], format="reel", body="Een reel")
        await c.patch(
            f"{BASE}/posts/{post['id']}",
            json={"media": [{"kind": "video", "url": "https://cdn.example/reel.mp4"}]},
            headers=r.headers,
        )
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
        assert post["status"] == "publishing"
        assert len(fake.containers) == 1

        for container in fake.containers.values():
            container["status"] = "FINISHED"
        await _rewind(r.tenant, post["id"])
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    # One container, one reel: the second sweep asked about the first, it did not start over.
    assert len(fake.containers) == 1
    assert len(fake.ig_media) == 1
    assert fake.writes(f"{IG_CLIENT}/media")[0].data["media_type"] == "REELS"


async def test_one_post_to_two_channels_is_one_post_each(client_for, fake) -> None:
    r = await ready(client_for, "post-both")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, assets=[r.page_id, r.instagram_id])
        await c.patch(
            f"{BASE}/posts/{post['id']}",
            json={"overrides": {r.instagram_id: "Korter, met #fietsen"}},
            headers=r.headers,
        )
        post = await _with_image(c, r, post)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        assert await run_worker(r.tenant) == 2
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    assert {t["channel"]: t["status"] for t in post["targets"]} == {
        "facebook": "published",
        "instagram": "published",
    }
    assert fake.writes("/photos")[0].data["caption"] == "Nieuwe collectie is binnen!"
    container = next(w for w in fake.writes("/media") if "image_url" in w.data)
    assert container.data["caption"] == "Korter, met #fietsen"


# --- when Meta says no, and when Meta says nothing -------------------------------------------- #


async def test_a_refusal_fails_the_delivery_and_tells_the_people_behind_it(
    client_for, fake
) -> None:
    r = await ready(client_for, "post-refused")
    fake.fail_next(
        "POST",
        "/feed",
        error(368, "Blocked", user_msg="Dit bericht is in strijd met ons beleid.", status=400),
    )
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
        inbox = (await c.get("/api/v1/notifications", headers=r.headers)).json()
        # And nothing retries it on its own: Meta said no, and a person reads why.
        await _rewind(r.tenant, post["id"])
        assert await run_worker(r.tenant) == 0
    assert post["status"] == "failed"
    target = post["targets"][0]
    assert target["status"] == "failed"
    assert target["last_error_code"] == "meta_policy_block"
    assert "in strijd met ons beleid" in target["last_error"]
    items = inbox["items"] if isinstance(inbox, dict) else inbox
    failed = [n for n in items if n["event_type"] == "meta.post_failed"]
    assert len(failed) == 1
    assert failed[0]["entity_id"] == post["id"]
    assert failed[0]["payload"]["channel"] == "facebook"
    assert failed[0]["payload"]["asset"] == "Nova Fietsen"
    assert len(fake.writes("/feed")) == 1


async def test_a_lost_answer_never_becomes_a_second_post(client_for, fake) -> None:
    """**The test this integration exists to pass.** The post is made and the answer is lost.
    A retry would post it twice under a client's name; the sweep looks instead, finds it, and
    adopts it."""
    r = await ready(client_for, "post-lost")
    fake.lose_next("POST", "/feed")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
        assert post["status"] == "publishing"
        assert post["targets"][0]["meta_post_id"] is None
        assert len(fake.posts) == 1

        # Too soon to look: a delivery that may still be in flight is not second-guessed.
        assert await run_worker(r.tenant) == 0

        await _rewind(r.tenant, post["id"])
        assert await run_worker(r.tenant) == 1
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    assert post["targets"][0]["meta_post_id"] == next(iter(fake.posts))
    assert len(fake.posts) == 1
    assert len(fake.writes("/feed")) == 1


async def test_a_post_that_demonstrably_did_not_land_is_tried_again(client_for, fake) -> None:
    r = await ready(client_for, "post-retry")
    fake.fail_next("POST", "/feed", error(2, "Service temporarily unavailable", status=500))
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        assert len(fake.posts) == 0
        await _rewind(r.tenant, post["id"])
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    assert len(fake.posts) == 1
    assert post["targets"][0]["attempts"] == 2


async def test_two_workers_share_one_winner(client_for, fake) -> None:
    """The claim is a conditional update, so the second worker finds nothing to claim — two
    replicas share no memory, and the database is what they share."""
    r = await ready(client_for, "post-race")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
    from tests.meta_helpers import system_publisher

    first, session_a = await system_publisher(r.tenant)
    second, session_b = await system_publisher(r.tenant)
    try:
        target_id = (await first.targets(post["id"]))[0].id
        now = datetime.now(UTC)
        assert await first._claim(target_id, "pending", now) is True  # noqa: SLF001
        await session_a.commit()
        assert await second._claim(target_id, "pending", now) is False  # noqa: SLF001
    finally:
        await session_a.close()
        await session_b.close()


async def test_a_dead_page_token_is_asked_for_again(client_for, fake) -> None:
    """The cached Page token is refused before anything is made, so trying once more with a
    fresh one is safe — and is the difference between a hiccup and a failed post."""
    r = await ready(client_for, "post-pagetoken")
    async with client_for(r.tenant.host) as c:
        await c.get(f"{BASE}/assets/{r.page_id}/published", headers=r.headers)
        fake.fail_next("POST", "/feed", error(190, "Invalid OAuth access token", subcode=460))
        post = await _draft(c, r)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    assert len(fake.posts) == 1


async def test_one_channel_failing_leaves_the_other_published(client_for, fake) -> None:
    r = await ready(client_for, "post-partial")
    fake.fail_next("POST", "media_publish", error(100, "Invalid parameter", status=400))
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r, assets=[r.page_id, r.instagram_id])
        post = await _with_image(c, r, post)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
        assert post["status"] == "partial"

        res = await c.post(f"{BASE}/posts/{post['id']}/retry", headers=r.headers)
        assert res.json()["status"] == "scheduled"
        await run_worker(r.tenant)
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    # The channel that had landed was left alone.
    assert len(fake.writes("/photos")) == 1
    assert len(fake.ig_media) == 1


# --- Meta's own scheduler -------------------------------------------------------------------- #


async def test_with_metas_scheduler_a_facebook_post_is_handed_over(client_for, fake) -> None:
    """The tenant's choice (docs/META.md §12): Meta holds the clock for Facebook, so the post
    shows in Business Suite's planner and goes out even if this instance is down. Instagram
    has no scheduler to hand anything to, and stays ours."""
    r = await ready(client_for, "post-native")
    async with client_for(r.tenant.host) as c:
        await c.put(f"{BASE}/settings", json={"facebook_scheduler": "meta"}, headers=r.headers)
        post = await _draft(c, r, assets=[r.page_id, r.instagram_id])
        post = await _with_image(c, r, post)
        when = datetime.now(UTC) + timedelta(days=3)
        await c.post(
            f"{BASE}/posts/{post['id']}/schedule",
            json={"scheduled_at": when.isoformat()},
            headers=r.headers,
        )
        assert await run_worker(r.tenant) == 1
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
        by_channel = {t["channel"]: t for t in post["targets"]}
        assert post["status"] == "scheduled"
        assert by_channel["facebook"]["status"] == "handed_over"
        assert by_channel["facebook"]["scheduler"] == "meta"
        assert by_channel["instagram"]["status"] == "pending"
        assert by_channel["instagram"]["scheduler"] == "schakl"
        write = fake.writes("/photos")[0]
        assert write.data["published"] == "false"
        assert int(write.data["scheduled_publish_time"]) == int(when.timestamp())
        remote = fake.posts[by_channel["facebook"]["meta_post_id"]]
        assert remote["is_published"] is False

        # The time comes. Meta publishes its half; we publish ours and confirm Meta's.
        remote["is_published"] = True
        assert await run_worker(r.tenant, now=when + timedelta(minutes=1)) == 2
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    assert len(fake.posts) == 1


async def test_a_time_too_near_for_metas_scheduler_is_published_by_us(client_for, fake) -> None:
    """Meta's scheduler wants ten minutes' notice. Five minutes ahead is not refused: it is
    published by our own worker at the time, which is what was asked for."""
    r = await ready(client_for, "post-native-near")
    async with client_for(r.tenant.host) as c:
        await c.put(f"{BASE}/settings", json={"facebook_scheduler": "meta"}, headers=r.headers)
        post = await _draft(c, r)
        when = datetime.now(UTC) + timedelta(minutes=5)
        await c.post(
            f"{BASE}/posts/{post['id']}/schedule",
            json={"scheduled_at": when.isoformat()},
            headers=r.headers,
        )
        assert await run_worker(r.tenant) == 0
        assert await run_worker(r.tenant, now=when + timedelta(seconds=30)) == 1
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["status"] == "published"
    assert "scheduled_publish_time" not in fake.writes("/feed")[0].data


async def test_changing_a_handed_over_post_takes_it_back_first(client_for, fake) -> None:
    r = await ready(client_for, "post-native-edit")
    async with client_for(r.tenant.host) as c:
        await c.put(f"{BASE}/settings", json={"facebook_scheduler": "meta"}, headers=r.headers)
        post = await _draft(c, r)
        await c.post(
            f"{BASE}/posts/{post['id']}/schedule",
            json={"scheduled_at": _in(days=2)},
            headers=r.headers,
        )
        await run_worker(r.tenant)
        assert len(fake.posts) == 1

        res = await c.patch(
            f"{BASE}/posts/{post['id']}", json={"body": "Toch anders"}, headers=r.headers
        )
        assert res.status_code == 200
        assert fake.posts == {}
        assert res.json()["targets"][0]["status"] == "pending"
        await run_worker(r.tenant)
        assert [p["message"] for p in fake.posts.values()] == ["Toch anders"]

        # And taking it back to a draft leaves nothing in Meta's planner.
        res = await c.post(f"{BASE}/posts/{post['id']}/unschedule", headers=r.headers)
        assert res.json()["status"] == "draft"
        assert fake.posts == {}


async def test_an_edit_meta_will_not_release_changes_nothing_here(client_for, fake) -> None:
    """Saved here and refused there is a post that contradicts the planner in Business Suite.
    So the edit is refused whole."""
    r = await ready(client_for, "post-native-locked")
    async with client_for(r.tenant.host) as c:
        await c.put(f"{BASE}/settings", json={"facebook_scheduler": "meta"}, headers=r.headers)
        post = await _draft(c, r)
        await c.post(
            f"{BASE}/posts/{post['id']}/schedule",
            json={"scheduled_at": _in(days=2)},
            headers=r.headers,
        )
        await run_worker(r.tenant)
        fake.fail_next(
            "DELETE", PAGE_CLIENT, error(200, "Only select developers may delete", status=403)
        )
        res = await c.patch(
            f"{BASE}/posts/{post['id']}", json={"body": "Toch anders"}, headers=r.headers
        )
        assert res.status_code == 409
        post = (await c.get(f"{BASE}/posts/{post['id']}", headers=r.headers)).json()
    assert post["body"] == "Nieuwe collectie is binnen!"
    assert post["targets"][0]["status"] == "handed_over"
    assert len(fake.posts) == 1


# --- the list -------------------------------------------------------------------------------- #


async def test_the_list_pages_filters_and_counts(client_for, fake) -> None:
    r = await ready(client_for, "post-list")
    async with client_for(r.tenant.host) as c:
        drafts = [await _draft(c, r, body=f"Bericht {n}") for n in range(4)]
        await c.post(f"{BASE}/posts/{drafts[0]['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        await c.post(f"{BASE}/posts/{drafts[1]['id']}/cancel", headers=r.headers)

        everything = (await c.get(f"{BASE}/posts", headers=r.headers)).json()
        working = (
            await c.get(f"{BASE}/posts", params={"status": "working"}, headers=r.headers)
        ).json()
        page = (
            await c.get(f"{BASE}/posts", params={"limit": 1, "offset": 1}, headers=r.headers)
        ).json()
        found = (await c.get(f"{BASE}/posts", params={"q": "Bericht 3"}, headers=r.headers)).json()
        instagram = (
            await c.get(f"{BASE}/posts", params={"channel": "instagram"}, headers=r.headers)
        ).json()
        counts = (await c.get(f"{BASE}/posts/counts", headers=r.headers)).json()
        bad = await c.get(f"{BASE}/posts", params={"status": "nonsense"}, headers=r.headers)
    assert everything["total"] == 4
    assert working["total"] == 2
    assert {p["status"] for p in working["items"]} == {"draft"}
    assert page["total"] == 4 and len(page["items"]) == 1
    assert [p["body"] for p in found["items"]] == ["Bericht 3"]
    assert instagram["total"] == 0
    assert counts == {
        "by_status": {"published": 1, "cancelled": 1, "draft": 2},
        "working": 2,
        "total": 4,
    }
    assert bad.status_code == 422


async def test_the_list_costs_the_same_at_any_length(client_for, fake, count_queries) -> None:
    """One query at three posts and one per post at three hundred passes every functional
    test either way, so the number is written down (docs/PERFORMANCE.md)."""
    r = await ready(client_for, "post-budget")
    async with client_for(r.tenant.host) as c:
        for n in range(2):
            await _draft(c, r, assets=[r.page_id, r.instagram_id], body=f"Bericht {n}")
        with count_queries() as small:
            res = await c.get(f"{BASE}/posts", headers=r.headers)
        assert res.status_code == 200
        for n in range(9):
            await _draft(c, r, assets=[r.page_id, r.instagram_id], body=f"Meer {n}")
        with count_queries() as large:
            res = await c.get(f"{BASE}/posts", headers=r.headers)
    assert res.json()["total"] == 11
    assert len(large) == len(small)
    assert len(large.matching("from meta_post_targets")) == 1
    assert len(large.matching("from meta_assets")) == 1


async def test_a_published_post_is_a_record_and_stays(client_for, fake) -> None:
    r = await ready(client_for, "post-record")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        await c.post(f"{BASE}/posts/{post['id']}/publish", headers=r.headers)
        await run_worker(r.tenant)
        res = await c.delete(f"{BASE}/posts/{post['id']}", headers=r.headers)
        assert res.status_code == 409
        assert res.json()["error"]["message"] == "errors.meta_post_published"
        res = await c.patch(f"{BASE}/posts/{post['id']}", json={"body": "x"}, headers=r.headers)
        assert res.status_code == 409

        draft = await _draft(c, r, body="Weg ermee")
        assert (await c.delete(f"{BASE}/posts/{draft['id']}", headers=r.headers)).status_code == 204
        assert (await c.get(f"{BASE}/posts/{draft['id']}", headers=r.headers)).status_code == 404


async def test_a_copy_carries_the_words_and_not_the_pictures(client_for, fake) -> None:
    r = await ready(client_for, "post-copy")
    async with client_for(r.tenant.host) as c:
        post = await _with_image(c, r, await _draft(c, r, assets=[r.page_id, r.instagram_id]))
        res = await c.post(f"{BASE}/posts/{post['id']}/duplicate", headers=r.headers)
    copy = res.json()
    assert res.status_code == 201
    assert copy["id"] != post["id"]
    assert copy["status"] == "draft"
    assert copy["body"] == post["body"]
    assert {t["channel"] for t in copy["targets"]} == {"facebook", "instagram"}
    assert copy["media"] == []


async def test_the_trail_holds_the_decisions_and_not_the_typing(client_for, fake) -> None:
    """A draft saves itself a moment after every pause, so an edit of a draft writes nothing:
    a line per pause would bury the five that matter. From the moment it is planned, a change
    to the words is a change to what was approved, and is written down."""
    r = await ready(client_for, "post-trail")
    async with client_for(r.tenant.host) as c:
        post = await _draft(c, r)
        await c.patch(f"{BASE}/posts/{post['id']}", json={"body": "Beter"}, headers=r.headers)
        draft_trail = await c.get(
            "/api/v1/activity",
            params={"entity_type": "meta_post", "entity_id": post["id"]},
            headers=r.headers,
        )
        assert [row["action"] for row in draft_trail.json()] == ["created"]
        await c.post(
            f"{BASE}/posts/{post['id']}/schedule",
            json={"scheduled_at": _in(days=1)},
            headers=r.headers,
        )
        await c.patch(f"{BASE}/posts/{post['id']}", json={"body": "Nog beter"}, headers=r.headers)
        res = await c.get(
            "/api/v1/activity",
            params={"entity_type": "meta_post", "entity_id": post["id"]},
            headers=r.headers,
        )
    assert res.status_code == 200
    body = res.json()
    rows = body["items"] if isinstance(body, dict) else body
    actions = [row["action"] for row in rows]
    assert "created" in actions
    assert "meta.post_scheduled" in actions
    assert "updated" in actions


# --- isolation and horizon ------------------------------------------------------------------- #


async def test_one_tenant_never_sees_anothers_posts(client_for, fake) -> None:
    a = await ready(client_for, "post-iso-a")
    async with client_for(a.tenant.host) as c:
        post = await _draft(c, a)
    b = await make_tenant("post-iso-b")
    headers = await auth_cookie(b.user)
    async with client_for(b.host) as c:
        assert (await c.get(f"{BASE}/posts", headers=headers)).json()["total"] == 0
        assert (await c.get(f"{BASE}/posts/{post['id']}", headers=headers)).status_code == 404
        res = await c.post(f"{BASE}/posts/{post['id']}/publish", headers=headers)
        assert res.status_code == 404
        res = await c.post(
            f"{BASE}/posts", json={"asset_ids": [a.page_id], "body": "x"}, headers=headers
        )
        assert res.status_code == 422


async def test_a_member_scoped_to_one_client_sees_that_clients_posts(client_for, fake) -> None:
    """The company horizon (§15): the list, its total, the detail and the assets all carry
    it — and what belongs to no client (the agency's own Page) stays visible to everyone."""
    r = await ready(client_for, "post-horizon")
    other = await company(r.tenant.org.id, "Andere Klant")
    scoped_user = User(
        id=uuid.uuid4(),
        email="beperkt@post-horizon.example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
    )
    async with async_session_maker() as session:
        session.add(scoped_user)
        await session.flush()
        await set_current_org(session, r.tenant.org.id)
        # An admin, so a leak would show as rows rather than hide behind a 403.
        membership = await add_membership(session, r.tenant.org.id, scoped_user.id, "admin")
        membership_id = str(membership.id)
        await session.commit()
    scoped = await auth_cookie(
        User(id=scoped_user.id, email=scoped_user.email, hashed_password="", is_active=True),
        r.tenant.org.id,
    )
    async with client_for(r.tenant.host) as c:
        mine = await _draft(c, r)
        own = await _draft(c, r, assets=[r.own_page_id], body="Van het bureau zelf")
        group = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Andere klanten"}, headers=r.headers
            )
        ).json()
        res = await c.put(
            f"/api/v1/companies/groups/{group['id']}/companies",
            json={"company_ids": [other]},
            headers=r.headers,
        )
        assert res.status_code == 204, res.text
        res = await c.put(
            f"/api/v1/companies/groups/{group['id']}/memberships",
            json={"membership_ids": [membership_id]},
            headers=r.headers,
        )
        assert res.status_code == 204, res.text

        listing = (await c.get(f"{BASE}/posts", headers=scoped)).json()
        assert [p["id"] for p in listing["items"]] == [own["id"]]
        assert listing["total"] == 1
        counts = (await c.get(f"{BASE}/posts/counts", headers=scoped)).json()
        assert counts["total"] == 1
        assert (await c.get(f"{BASE}/posts/{mine['id']}", headers=scoped)).status_code == 404
        assets = (await c.get(f"{BASE}/assets", headers=scoped)).json()["items"]
        assert {a["id"] for a in assets} == {r.own_page_id}
        assert (await c.get(f"{BASE}/assets/{r.page_id}", headers=scoped)).status_code == 404
        # Nor may a post be written onto a channel outside the horizon.
        res = await c.post(
            f"{BASE}/posts", json={"asset_ids": [r.page_id], "body": "x"}, headers=scoped
        )
        assert res.status_code == 422
        # The owner is never restricted.
        assert (await c.get(f"{BASE}/posts", headers=r.headers)).json()["total"] == 2
