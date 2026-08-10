"""Turning a frozen snapshot into prose (issue #300) — layer four of the prompt stack.

Everything here goes through :class:`~app.core.ai.service.AIService`, so the tenant's provider,
their key, the ``reporting`` feature toggle, the monthly token budget and the usage meter all
apply without this module knowing any of them exist. That is the whole reason ``core/ai`` is a
core: the workflow this replaces pinned ``gpt-5-mini`` in two nodes against one person's
personal OpenAI credential, invisible to the agency and uncapped.

Two properties are worth being explicit about, because both are the difference between a
feature people trust and one they turn off:

**The model writes prose and never arithmetic.** Every number in the document comes from the
snapshot and is rendered by the template. The model is handed the finished figures and asked to
describe them. Nothing it returns is parsed as a number, so a hallucinated one cannot reach the
page — the worst it can do is describe a real number wrongly, which a reviewer can see.

**A banned phrase is checked, not merely requested.** The tone lists what this agency never
says; asking a model nicely is not a control. :func:`banned_phrases_used` runs over the output
and puts what it finds on the run's warnings, where the reviewer sees it before the client does.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.ai.providers import AIProviderError, ChatMessage
from app.core.ai.service import AIService
from app.errors import AppError
from app.modules.reporting import prompts

logger = logging.getLogger("schakl.reporting")

#: The AI-core feature key this rides. Already in ``AI_FEATURES`` — the report draft (#130)
#: claimed it first, and this is what it was always for.
FEATURE = "reporting"

#: A month of tables for a large client is a big document. This is generous enough for a real
#: one and bounded enough that a runaway payload cannot spend a tenant's whole budget on a
#: single report.
MAX_INPUT_CHARS = 60_000
MAX_OUTPUT_TOKENS = 4096
MAX_SECTION_TOKENS = 900

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def parse_json_object(text: str) -> dict[str, Any]:
    """The model's reply as an object, however it chose to wrap it.

    Models emit a bare object most of the time and a fenced one the rest of it, and both mean
    the same thing. A reply that cannot be read at all returns ``{}`` rather than raising: an
    unparseable narrative costs the report its prose, not its numbers, and a document with
    empty paragraphs and correct tables is something a reviewer can finish by hand.
    """
    if not text:
        return {}
    cleaned = _FENCE.sub("", text.strip()).strip()
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        # A model that prefaced its JSON with a sentence: take the outermost object.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            logger.warning("reporting: narrative reply was not JSON (%s chars)", len(cleaned))
            return {}
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except ValueError:
            logger.warning("reporting: narrative reply could not be recovered")
            return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): value.strip() if isinstance(value, str) else _flatten(value)
        for key, value in parsed.items()
    }


def _flatten(value: Any) -> str:
    """A key the model answered with a list or an object, rendered as text.

    Asked for a string it usually returns one; when it does not, the honest options are to
    drop the section or to read what it wrote. Reading it is better — the content is right and
    only its shape is wrong.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(_flatten(item) for item in value if item)
    if isinstance(value, dict):
        return "\n".join(_flatten(item) for item in value.values() if item)
    return str(value)


def banned_phrases_used(text: str, banned: list[str]) -> list[str]:
    """Which of the tone's forbidden phrasings survived into the output.

    Word-boundary matching, so "advies" does not fire on "adviesbureau" in a client's own
    name — a false positive on every report of one client trains the reviewer to ignore the
    warning, which costs more than the true positives are worth.
    """
    if not text or not banned:
        return []
    lowered = text.lower()
    found: list[str] = []
    for phrase in banned:
        needle = str(phrase).strip().lower()
        if not needle:
            continue
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", lowered):
            found.append(needle)
    return found


def _document(presented: dict[str, Any], profile: dict[str, Any] | None) -> str:
    """The user message: the report **as the document prints it**, and the client's facts.

    ``presented`` is :mod:`app.modules.reporting.present`'s output, never the raw snapshot —
    every key a label, every value the exact string the table carries. Handing over the
    snapshot is what put ``totalUsers``, ``0.4595`` and ``compare_sessions 61, delta 21.3``
    into a client's Dutch paragraph: the model was quoting its input faithfully, and its input
    was a database row.

    The profile rides *inside* it, under its own key, because that is what makes it data. The
    system prompt has already said everything in here is data; putting the profile anywhere
    else would quietly contradict that.
    """
    payload = {"client_profile": profile or {}, "report": presented}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_INPUT_CHARS:
        # Presenting already caps every table at `present.MAX_ROWS`, so overrunning here means
        # a report with a great many *sections*. Truncating is the last resort it always was.
        text = text[:MAX_INPUT_CHARS]
    return text


async def write_narrative(
    service: AIService,
    *,
    presented: dict[str, Any],
    profile: dict[str, Any] | None,
    tone: dict[str, Any] | None,
    sections: list[tuple[str, str]],
    locale: str,
    brand: str,
    period_label: str,
    compare_label: str | None,
    internal: bool,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """One model turn for the whole document. Returns ``(narrative, warnings)``.

    One turn rather than one per section: the sections of a report are about the same month
    and the opening summary has to know what the rest says, so writing them apart produces a
    summary that contradicts the section under it.
    """
    system = (
        prompts.internal_system(
            locale=locale,
            brand=brand,
            period_label=period_label,
            compare_label=compare_label,
            sections=sections,
        )
        if internal
        else prompts.client_system(
            locale=locale,
            brand=brand,
            period_label=period_label,
            compare_label=compare_label,
            tone=tone,
            sections=sections,
        )
    )
    warnings: list[dict[str, str]] = []
    try:
        text, _ = await service.complete(
            FEATURE,
            system=system,
            messages=[ChatMessage(role="user", content=_document(presented, profile))],
            disable_tools=True,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
    except AIProviderError as exc:
        logger.warning("reporting: narrative generation failed: %s", exc)
        return {}, [{"code": "reporting.warning.ai_failed", "detail": str(exc)[:200]}]
    except AppError as exc:
        # A budget refusal is not a crash — the report keeps its numbers and says why the
        # prose is missing, which is a state a reviewer can act on.
        return {}, [{"code": "reporting.warning.ai_unavailable", "detail": exc.message_key}]
    finally:
        await service.flush_usage(FEATURE)

    narrative = {key: value for key, value in parse_json_object(text).items() if value}
    if not narrative:
        warnings.append({"code": "reporting.warning.ai_empty", "detail": ""})
    banned = (tone or {}).get("banned_phrases") or []
    if not internal:
        for key, value in narrative.items():
            used = banned_phrases_used(value, banned)
            if used:
                warnings.append(
                    {"code": "reporting.warning.banned_phrase",
                     "detail": f"{key}: {', '.join(used)}"}
                )
    return narrative, warnings


async def rewrite_section(
    service: AIService,
    *,
    presented_section: dict[str, Any],
    profile: dict[str, Any] | None,
    tone: dict[str, Any] | None,
    section_key: str,
    brief: str,
    locale: str,
    brand: str,
    period_label: str,
    internal: bool,
) -> tuple[str, list[dict[str, str]]]:
    """Rewrite one paragraph, against that section's data only."""
    system = prompts.section_system(
        locale=locale,
        brand=brand,
        period_label=period_label,
        tone=tone,
        section_key=section_key,
        brief=brief,
        internal=internal,
    )
    try:
        text, _ = await service.complete(
            FEATURE,
            system=system,
            messages=[
                ChatMessage(
                    role="user",
                    content=_document({"sections": {section_key: presented_section}}, profile),
                )
            ],
            disable_tools=True,
            max_tokens=MAX_SECTION_TOKENS,
        )
    except AIProviderError as exc:
        logger.warning("reporting: section rewrite failed: %s", exc)
        return "", [{"code": "reporting.warning.ai_failed", "detail": str(exc)[:200]}]
    finally:
        await service.flush_usage(FEATURE)
    cleaned = _FENCE.sub("", (text or "").strip()).strip()
    warnings: list[dict[str, str]] = []
    if not internal:
        used = banned_phrases_used(cleaned, (tone or {}).get("banned_phrases") or [])
        if used:
            warnings.append(
                {"code": "reporting.warning.banned_phrase",
                 "detail": f"{section_key}: {', '.join(used)}"}
            )
    return cleaned, warnings
