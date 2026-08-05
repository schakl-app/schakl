"""Building and comparing a Cloudflare Redirect Rule (epic #278). Business-licensed.

Pure functions, no I/O — which is the point: the expression a domain-wide redirect compiles to
is the single most breakable thing in this module (one unescaped quote and the rule either fails
to save or matches the wrong host), and it is worth being able to assert on it without a
Cloudflare in the loop.

Three jobs live here:

1. :func:`build_rule` — intent → the rule body Cloudflare wants.
2. :func:`compare` — the rule body we *would* build vs the one Cloudflare *has*, so an edit made
   in the Cloudflare dashboard reads as drift with named fields instead of being silently
   overwritten on the next save.
3. :func:`redirect_loop_target` — the guard that refuses a redirect pointing back at the host it
   redirects. Cloudflare will happily save that rule; the browser reports it as
   ``ERR_TOO_MANY_REDIRECTS`` and the client's site is down.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.modules.cloudflare.client import RULE_MARKER

#: What the rule's description says at Cloudflare. Carries the marker so a human reading the
#: dashboard knows why it is there, and so a reconcile can recognise our rule if the stored id
#: is ever lost (it still never *deletes* on the strength of a description alone).
RULE_DESCRIPTION = f"{RULE_MARKER}: domain redirect"


def _quote(value: str) -> str:
    """A string literal for a Cloudflare filter expression.

    Backslash first, then the quote — the other order double-escapes. A hostname cannot contain
    either, but a *target URL* can, and the target is interpolated into a ``concat()``.
    """
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def host_expression(apex: str, *, include_subdomains: bool) -> str:
    """The filter that decides which requests this redirect answers.

    ``ends_with(http.host, ".apex")`` rather than a wildcard: it matches ``www.klant.nl`` and
    ``shop.klant.nl`` but never ``nietklant.nl``, which a naive ``contains`` would.
    """
    apex_literal = _quote(apex)
    if not include_subdomains:
        return f"(http.host eq {apex_literal})"
    return f"(http.host eq {apex_literal} or ends_with(http.host, {_quote('.' + apex)}))"


def _target_base(target_url: str) -> str:
    """The target with any trailing slash removed, so ``concat`` never yields ``//pad``."""
    return target_url[:-1] if target_url.endswith("/") else target_url


def build_rule(
    *,
    apex: str,
    target_url: str,
    status_code: int,
    preserve_path: bool,
    preserve_query: bool,
    include_subdomains: bool,
) -> dict[str, Any]:
    """The rule body for one domain-wide redirect.

    ``target_url`` is sent as a **static value** when the path is not preserved and as an
    expression when it is. Cloudflare accepts both, and the static form is what makes the common
    "send everything to the new homepage" case readable in their dashboard.
    """
    if preserve_path:
        target: dict[str, Any] = {
            "expression": f"concat({_quote(_target_base(target_url))}, http.request.uri.path)"
        }
    else:
        target = {"value": target_url}
    return {
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "status_code": status_code,
                "target_url": target,
                "preserve_query_string": preserve_query,
            }
        },
        "expression": host_expression(apex, include_subdomains=include_subdomains),
        "description": RULE_DESCRIPTION,
        "enabled": True,
    }


def _from_value(rule: dict[str, Any]) -> dict[str, Any]:
    params = rule.get("action_parameters") or {}
    value = params.get("from_value") or {}
    return value if isinstance(value, dict) else {}


def _target_of(rule: dict[str, Any]) -> Any:
    target = _from_value(rule).get("target_url")
    if isinstance(target, dict):
        return target.get("value") if "value" in target else target.get("expression")
    return target


def compare(desired: dict[str, Any], live: dict[str, Any]) -> list[str]:
    """Which fields of our rule Cloudflare disagrees with — ``[]`` means in sync.

    Compares only what schakl sets. Cloudflare adds ``id``, ``version``, ``last_updated`` and a
    normalised ``ref``; treating those as drift would report every rule as changed forever.
    """
    differences: list[str] = []
    if live.get("action") != desired.get("action"):
        differences.append("action")
    if (live.get("expression") or "") != (desired.get("expression") or ""):
        differences.append("expression")
    if _target_of(live) != _target_of(desired):
        differences.append("target_url")
    desired_from, live_from = _from_value(desired), _from_value(live)
    if live_from.get("status_code") != desired_from.get("status_code"):
        differences.append("status_code")
    if bool(live_from.get("preserve_query_string")) != bool(
        desired_from.get("preserve_query_string")
    ):
        differences.append("preserve_query_string")
    # ``enabled`` defaults to true and is often absent on a read; only an explicit false differs.
    if live.get("enabled") is False and desired.get("enabled") is not False:
        differences.append("enabled")
    return differences


def target_host(target_url: str) -> str:
    """The hostname a target URL points at, lowercased and without a port. ``""`` if unparseable."""
    parsed = urlsplit(target_url if "//" in target_url else f"//{target_url}")
    return (parsed.hostname or "").lower().rstrip(".")


def redirect_loop_target(
    *, apex: str, target_url: str, include_subdomains: bool
) -> bool:
    """Would this redirect send the browser straight back into its own match set?

    The apex itself always loops. A subdomain of it loops only when ``include_subdomains`` puts
    that subdomain inside the rule's expression — redirecting ``klant.nl`` to
    ``nieuw.klant.nl`` is perfectly sensible with subdomains off and an infinite loop with them
    on, which is the trap this exists to catch.
    """
    host = target_host(target_url)
    apex = apex.lower().rstrip(".")
    if not host:
        return False
    if host == apex:
        return True
    return include_subdomains and host.endswith(f".{apex}")


def find_our_rule(ruleset: dict[str, Any] | None, rule_id: str | None) -> dict[str, Any] | None:
    """Our rule inside a fetched ruleset — by stored id, never by description.

    Matching on the description would let a tenant's hand-written rule named ``schakl: …`` be
    adopted and then edited or deleted by us. The id is the only safe key; a lost id means the
    rule reads as ``missing`` and is recreated, which is recoverable, whereas deleting somebody
    else's rule is not.
    """
    if not rule_id or not ruleset:
        return None
    for rule in ruleset.get("rules") or []:
        if isinstance(rule, dict) and rule.get("id") == rule_id:
            return rule
    return None


def other_redirect_rules(
    ruleset: dict[str, Any] | None, rule_id: str | None
) -> list[dict[str, Any]]:
    """Every *other* rule in the redirect ruleset, in evaluation order.

    Reported as conflicts rather than resolved: Cloudflare evaluates the list top-down and the
    first match wins, so a tenant rule above ours silently wins — and we cannot evaluate their
    expression to know whether it matches this host. Naming them is honest; guessing is not.
    """
    if not ruleset:
        return []
    return [
        rule
        for rule in (ruleset.get("rules") or [])
        if isinstance(rule, dict) and rule.get("id") != rule_id
    ]


def forwarding_page_rules(page_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Legacy Page Rules on this zone that forward a URL.

    A forwarding Page Rule is the most common way a domain "already redirects" without schakl
    knowing, and it is invisible from the Redirect Rules screen — which is exactly why an
    agency taking over a client's Cloudflare account gets a status report that mentions it.
    Page Rules are per-zone, so every one returned is already this domain's; which URLs it
    actually catches is in its own pattern, reported verbatim rather than second-guessed.
    """
    return [
        rule
        for rule in page_rules or []
        if isinstance(rule, dict)
        and any(
            isinstance(action, dict) and action.get("id") == "forwarding_url"
            for action in rule.get("actions") or []
        )
    ]


def page_rule_pattern(rule: dict[str, Any]) -> str:
    """The URL pattern a Page Rule matches, for the conflict report."""
    for target in rule.get("targets") or []:
        if isinstance(target, dict) and target.get("target") == "url":
            constraint = target.get("constraint") or {}
            if isinstance(constraint, dict):
                return str(constraint.get("value") or "")
    return ""
