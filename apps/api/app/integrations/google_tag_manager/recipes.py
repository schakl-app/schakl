"""Turning "measure when somebody asks for a quote" into GTM's own vocabulary.

Business-licensed — see LICENSE.

**Why a recipe exists at all.** A tag is a `type` string plus an array of key/value `Parameter`
objects whose legal keys are decided by the tag *template*, and nothing in the API document says
what they are: the GA4 event tag wants ``measurementIdOverride`` and refuses ``measurementId``,
the Google Ads conversion tag wants ``conversionId``/``conversionLabel``, and a form-submission
trigger's *Check Validation* option is only legal when the trigger also has a filter. A model — or
a person — composing that from first principles gets it wrong, and the interesting half of getting
it wrong is silent: a tag that fires into nothing looks exactly like a tag that works.

**Why the recipe is deliberately small.** Two tag kinds and six trigger kinds, chosen because they
are what an agency sets up over and over and because their parameter vocabulary is short enough to
state honestly. Everything else goes through the raw ``POST …/tags`` endpoint, where the caller
writes the tag body and **GTM's own validator is the judge**. That is a better answer than a
half-modelled recipe: GTM validates parameter keys against the template and answers 400 naming the
field, so a hand-written body fails loudly where a wrong recipe would deploy quietly.

**What it never does is guess a value.** A measurement id, a conversion id and a CSS selector all
come from the caller. There is no "we'll find the GA4 property" step, because picking the wrong
one sends a client's conversions to somebody else's property and nothing on any screen would say
so.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.errors import AppError

#: The built-in variable a filter on the page address needs. Enabled in every web container by
#: default — requested anyway, because "by default" is a claim about containers we did not make.
BUILT_IN_PAGE_URL = "pageUrl"
#: What a CSS-selector click filter reads. Off by default in a fresh container, and a trigger
#: referencing a variable that does not exist is accepted by the API and fires never.
BUILT_IN_CLICK_ELEMENT = "clickElement"


class TriggerKind(StrEnum):
    """When the tag should fire. Six, and each maps to exactly one GTM trigger type."""

    PAGE_VIEW = "page_view"
    FORM_SUBMIT = "form_submit"
    LINK_CLICK = "link_click"
    ELEMENT_CLICK = "element_click"
    ELEMENT_VISIBILITY = "element_visibility"
    CUSTOM_EVENT = "custom_event"


_GTM_TRIGGER_TYPE: dict[str, str] = {
    TriggerKind.PAGE_VIEW: "pageview",
    TriggerKind.FORM_SUBMIT: "formSubmission",
    TriggerKind.LINK_CLICK: "linkClick",
    TriggerKind.ELEMENT_CLICK: "click",
    TriggerKind.ELEMENT_VISIBILITY: "elementVisibility",
    TriggerKind.CUSTOM_EVENT: "customEvent",
}


def _template(key: str, value: str) -> dict[str, Any]:
    return {"type": "template", "key": key, "value": value}


def _boolean(key: str, value: bool) -> dict[str, Any]:
    # GTM's booleans are the *strings* "true"/"false" inside a typed parameter, not JSON booleans.
    return {"type": "boolean", "key": key, "value": "true" if value else "false"}


def _condition(condition_type: str, left: str, right: str) -> dict[str, Any]:
    """One GTM ``Condition``: ``arg0`` is what is read, ``arg1`` is what it is compared to."""
    return {
        "type": condition_type,
        "parameter": [_template("arg0", left), _template("arg1", right)],
    }


def _invalid(field: str, key: str) -> AppError:
    return AppError("validation", "errors.validation", status_code=422, fields={field: key})


def required_built_ins(kind: str, *, url_contains: str | None) -> tuple[str, ...]:
    """The built-in variables this trigger reads, which must be switched on before it will fire.

    Stated here rather than left to the container's defaults, because the failure is invisible:
    GTM happily stores a trigger whose ``{{Click Element}}`` resolves to nothing, and the tag
    then never fires with no error anywhere to read.
    """
    needed: list[str] = []
    if url_contains:
        needed.append(BUILT_IN_PAGE_URL)
    if kind == TriggerKind.ELEMENT_CLICK:
        needed.append(BUILT_IN_CLICK_ELEMENT)
    return tuple(dict.fromkeys(needed))


def build_trigger(
    name: str,
    kind: str,
    *,
    url_contains: str | None = None,
    event_name: str | None = None,
    selector: str | None = None,
    visible_percent: int | None = None,
) -> dict[str, Any]:
    """One GTM ``Trigger`` body for the recipe's vocabulary.

    ``url_contains`` narrows any kind to pages whose address contains it — the ordinary "only on
    the contact page" ask, expressed once rather than per kind.
    """
    gtm_type = _GTM_TRIGGER_TYPE.get(kind)
    if gtm_type is None:
        raise _invalid("trigger_kind", "errors.gtm_trigger_kind_unknown")

    trigger: dict[str, Any] = {"name": name, "type": gtm_type}
    filters: list[dict[str, Any]] = []
    if url_contains:
        filters.append(_condition("contains", "{{Page URL}}", url_contains))

    if kind == TriggerKind.CUSTOM_EVENT:
        if not event_name:
            raise _invalid("event_name", "errors.gtm_event_name_required")
        # ``{{_event}}`` is GTM's own name for the dataLayer event, and is readable without any
        # built-in variable being switched on — unlike ``{{Event}}``, which is one.
        trigger["customEventFilter"] = [_condition("equals", "{{_event}}", event_name)]

    if kind == TriggerKind.ELEMENT_CLICK:
        if not selector:
            raise _invalid("selector", "errors.gtm_selector_required")
        filters.append(_condition("cssSelector", "{{Click Element}}", selector))

    if kind == TriggerKind.ELEMENT_VISIBILITY:
        if not selector:
            raise _invalid("selector", "errors.gtm_selector_required")
        trigger["selector"] = _template("selector", selector)
        # Half the element on screen — GTM's own default in the interface, stated here so it is
        # in one place rather than restated by every caller that has no opinion.
        trigger["visiblePercentageMin"] = _template(
            "visiblePercentageMin", str(visible_percent or 50)
        )
        trigger["parameter"] = [
            _template("selectorType", "CSS"),
            _template("firingFrequency", "ONCE"),
        ]

    if kind in (TriggerKind.FORM_SUBMIT, TriggerKind.LINK_CLICK):
        # Hold the browser briefly so the tag actually gets to fire before the page unloads.
        trigger["waitForTags"] = _boolean("waitForTags", True)
        trigger["waitForTagsTimeout"] = _template("waitForTagsTimeout", "2000")
        # *Check Validation* is only legal on a trigger that fires on **some** forms, so it is
        # set exactly when there is something to narrow by. Setting it unconditionally is the
        # mistake: GTM refuses the trigger, and the refusal names an option nobody chose.
        trigger["checkValidation"] = _boolean("checkValidation", bool(filters))

    if filters:
        trigger["filter"] = filters
    return trigger


def build_ga4_event_tag(
    name: str,
    *,
    event_name: str,
    measurement_id: str,
    firing_trigger_ids: list[str],
) -> dict[str, Any]:
    """A GA4 event tag (``gaawe``).

    ``measurementIdOverride`` — **not** ``measurementId``, which is the single most common way
    this goes wrong. GTM answers ``vendorTemplate.parameter.measurementIdOverride: The value must
    not be empty`` for the wrong key, which is a loud failure and still an hour of somebody's day.
    """
    if not event_name.strip():
        raise _invalid("event_name", "errors.gtm_event_name_required")
    if not measurement_id.strip():
        raise _invalid("measurement_id", "errors.gtm_measurement_id_required")
    return {
        "name": name,
        "type": "gaawe",
        "parameter": [
            _template("eventName", event_name.strip()),
            _template("measurementIdOverride", measurement_id.strip()),
        ],
        "firingTriggerId": list(firing_trigger_ids),
    }


def build_ads_conversion_tag(
    name: str,
    *,
    conversion_id: str,
    conversion_label: str,
    firing_trigger_ids: list[str],
    conversion_value: str | None = None,
    currency_code: str | None = None,
) -> dict[str, Any]:
    """A Google Ads conversion tag (``awct``).

    ``enableConversionLinker`` is on and is not an option here: without it the tag reports
    conversions it cannot attribute to a click, which is a number that looks right and is not.
    """
    if not conversion_id.strip():
        raise _invalid("conversion_id", "errors.gtm_conversion_id_required")
    if not conversion_label.strip():
        raise _invalid("conversion_label", "errors.gtm_conversion_label_required")
    parameters = [
        _template("conversionId", conversion_id.strip()),
        _template("conversionLabel", conversion_label.strip()),
        _boolean("enableConversionLinker", True),
    ]
    if conversion_value:
        parameters.append(_template("conversionValue", conversion_value))
    if currency_code:
        parameters.append(_template("currencyCode", currency_code.strip().upper()))
    return {
        "name": name,
        "type": "awct",
        "parameter": parameters,
        "firingTriggerId": list(firing_trigger_ids),
    }
