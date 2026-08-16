"""The vendor vocabulary, in isolation.

No database and no network: these assert the exact JSON that leaves for Google, because every
mistake this file can make is one GTM either refuses with a message nobody reads or — worse —
accepts, stores, and never fires.
"""

from __future__ import annotations

import pytest

from app.errors import AppError
from app.integrations.google_tag_manager import recipes


def _param(body: dict, key: str) -> dict | None:
    for entry in body.get("parameter") or []:
        if entry.get("key") == key:
            return entry
    return None


def test_a_ga4_event_tag_uses_measurement_id_override() -> None:
    """The single most common way this goes wrong: ``measurementId`` is silently the wrong key."""
    tag = recipes.build_ga4_event_tag(
        "Offerte aangevraagd",
        event_name="generate_lead",
        measurement_id="G-ABC123",
        firing_trigger_ids=["77"],
    )
    assert tag["type"] == "gaawe"
    assert _param(tag, "measurementIdOverride") == {
        "type": "template",
        "key": "measurementIdOverride",
        "value": "G-ABC123",
    }
    assert _param(tag, "measurementId") is None
    assert tag["firingTriggerId"] == ["77"]


def test_a_ga4_event_tag_refuses_to_invent_a_measurement_id() -> None:
    """Guessing sends a client's conversions to somebody else's property, silently."""
    with pytest.raises(AppError) as raised:
        recipes.build_ga4_event_tag(
            "x", event_name="generate_lead", measurement_id="", firing_trigger_ids=[]
        )
    assert raised.value.fields == {"measurement_id": "errors.gtm_measurement_id_required"}


def test_an_ads_conversion_tag_always_enables_the_conversion_linker() -> None:
    """Without it the tag reports conversions it cannot attribute — a number that looks right."""
    tag = recipes.build_ads_conversion_tag(
        "Aankoop",
        conversion_id="1006772047",
        conversion_label="0L_dCLyI84sBEM--iOAD",
        firing_trigger_ids=["9"],
        currency_code="eur",
    )
    assert tag["type"] == "awct"
    assert _param(tag, "enableConversionLinker") == {
        "type": "boolean",
        "key": "enableConversionLinker",
        "value": "true",
    }
    assert _param(tag, "currencyCode")["value"] == "EUR"


def test_a_form_trigger_only_checks_validation_when_it_has_a_filter() -> None:
    """GTM refuses *Check Validation* on a trigger that fires on every form, and the refusal
    names an option the user never chose."""
    narrow = recipes.build_trigger("Contact", "form_submit", url_contains="/contact")
    assert narrow["type"] == "formSubmission"
    assert narrow["checkValidation"] == {
        "type": "boolean",
        "key": "checkValidation",
        "value": "true",
    }
    assert narrow["filter"][0]["parameter"][0]["value"] == "{{Page URL}}"

    broad = recipes.build_trigger("Alle formulieren", "form_submit")
    assert broad["checkValidation"]["value"] == "false"
    assert "filter" not in broad


def test_a_custom_event_trigger_reads_the_dataLayer_event_without_a_built_in() -> None:
    """``{{_event}}`` needs no variable switched on; ``{{Event}}`` is one, and would fire never."""
    trigger = recipes.build_trigger("Lead", "custom_event", event_name="lead_submitted")
    assert trigger["type"] == "customEvent"
    condition = trigger["customEventFilter"][0]
    assert condition["type"] == "equals"
    assert condition["parameter"][0]["value"] == "{{_event}}"
    assert condition["parameter"][1]["value"] == "lead_submitted"


def test_a_click_trigger_declares_the_built_in_variable_it_reads() -> None:
    """A trigger referring to a variable that does not exist is stored happily and fires never."""
    trigger = recipes.build_trigger("CTA", "element_click", selector=".cta")
    assert trigger["type"] == "click"
    assert trigger["filter"][0]["type"] == "cssSelector"
    assert recipes.required_built_ins("element_click", url_contains=None) == ("clickElement",)
    assert recipes.required_built_ins("page_view", url_contains="/x") == ("pageUrl",)
    assert recipes.required_built_ins("page_view", url_contains=None) == ()


def test_an_unknown_trigger_kind_is_refused_rather_than_guessed() -> None:
    with pytest.raises(AppError) as raised:
        recipes.build_trigger("x", "telepathy")
    assert raised.value.fields == {"trigger_kind": "errors.gtm_trigger_kind_unknown"}
