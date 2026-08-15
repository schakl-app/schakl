"""Building and comparing a Cloudflare Redirect Rule (epic #278). Business-licensed.

Pure functions, no I/O — which is the point: the expression a domain-wide redirect compiles to
is the single most breakable thing in this module (one unescaped quote and the rule either fails
to save or matches the wrong host), and it is worth being able to assert on it without a
Cloudflare in the loop.

Five jobs live here:

0. :func:`edited_rule` — an *existing* rule body plus a new intent → the body to PUT back. Its
   own job rather than a flag on :func:`build_rule`, because it is defined by what it refuses to
   rebuild: a match set schakl cannot write is carried over untouched, so changing where an
   inherited redirect goes can never change what it catches.
1. :func:`build_rule` — intent → the rule body Cloudflare wants.
2. :func:`compare` — the rule body we *would* build vs the one Cloudflare *has*, so an edit made
   in the Cloudflare dashboard reads as drift with named fields instead of being silently
   overwritten on the next save.
3. :func:`redirect_loop_target` — the guard that refuses a redirect pointing back at the host it
   redirects. Cloudflare will happily save that rule; the browser reports it as
   ``ERR_TOO_MANY_REDIRECTS`` and the client's site is down.
4. :func:`rule_intent` / :func:`domain_wide_for` — the same trip **backwards**: reading a rule
   somebody else wrote back into the intent that would have produced it, and deciding whether it
   redirects this whole domain. That is what lets an inherited redirect be *described* on the
   panel instead of merely reported, and it is the narrowing that makes writing
   ``Domain.status = redirect`` off somebody else's rule safe.

The two backwards functions answer **different questions on purpose**, and the gap between them
is deliberate. ``domain_wide_for`` asks "does this rule redirect the whole domain?" — the input
to a status the tenant will see. ``rule_intent`` asks the stricter "could schakl have written
this?" — the input to adoption, which claims ownership. Cloudflare's list form,
``http.host in {"klant.nl" "www.klant.nl"}``, is exactly where they part: it plainly redirects
the domain, and it is not a rule any intent of ours produces, so it is listed and flagged
domain-wide but never offered for adoption.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from app.integrations.cloudflare.client import RULE_MARKER

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


#: ``concat("<base>", http.request.uri.path)`` — the target form :func:`build_rule` emits when the
#: path is preserved. Anchored, so a longer expression that merely *starts* with a concat is not
#: read as one: what we can put back together is exactly what we know how to take apart.
_CONCAT_TARGET = re.compile(
    r'^concat\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*http\.request\.uri\.path\s*\)$'
)

#: ``http.host in {"a" "b"}`` — Cloudflare's own list operator, which their dashboard emits for
#: the very common "apex and www" rule. Not a shape any of our intents produces.
_HOST_SET = re.compile(r"^http\.host in \{([^{}]*)\}$")

_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _unquote(value: str) -> str:
    """Undo :func:`_quote` in one pass.

    Sequential ``replace`` calls get ``a\\\\"b`` wrong depending on which order they run in; a
    single scan cannot, and the value being unescaped is a URL somebody's site depends on.
    """
    out: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    return "".join(out)


def _wrapped_in_parens(text: str) -> bool:
    """Is the whole of ``text`` inside one pair of parentheses?

    ``(a) or (b)`` is not, and stripping its ends would produce ``a) or (b`` — an expression that
    compares equal to nothing and would quietly make every such rule unreadable.
    """
    if not (text.startswith("(") and text.endswith(")")):
        return False
    depth = 0
    quoted = False
    for index, char in enumerate(text):
        if char == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
    return False


def _normalise_expression(expression: str) -> str:
    """A filter expression in a form two of them can be compared in.

    Cloudflare re-formats what it stores, so a byte comparison against what we sent answers the
    wrong question. Collapsed whitespace, no redundant outer parentheses, and ``lower(http.host)``
    folded to ``http.host`` (the dashboard adds it; it changes nothing — a hostname arrives
    lowercased). Nothing beyond that: normalising more would mean *interpreting* an expression
    language we do not evaluate, and the whole point of this file is that we never guess.
    """
    text = " ".join((expression or "").split())
    text = text.replace("lower(http.host)", "http.host")
    while _wrapped_in_parens(text):
        text = text[1:-1].strip()
    return text


def _host_forms(apex: str) -> dict[str, bool]:
    """Our two host expressions, normalised, mapped to the ``include_subdomains`` each means."""
    return {
        _normalise_expression(host_expression(apex, include_subdomains=flag)): flag
        for flag in (False, True)
    }


def rule_target(rule: dict[str, Any]) -> tuple[str, bool] | None:
    """``(target_url, preserve_path)`` for a live rule, or ``None`` when it cannot be read.

    Both target forms :func:`build_rule` emits are understood, and the *shape* is what says
    whether the path is preserved — there is no separate flag at Cloudflare to read it off.
    """
    target = _from_value(rule).get("target_url")
    if isinstance(target, str):
        return (target, False) if target else None
    if not isinstance(target, dict):
        return None
    if "value" in target:
        value = target.get("value")
        return (value, False) if isinstance(value, str) and value else None
    expression = target.get("expression")
    if not isinstance(expression, str):
        return None
    match = _CONCAT_TARGET.match(" ".join(expression.split()))
    if match is None:
        return None
    base = _unquote(match.group(1))
    return (base, True) if base else None


def rule_scope(rule: dict[str, Any], apex: str) -> bool | None:
    """``include_subdomains`` for a rule whose match set schakl can rewrite; ``None`` otherwise.

    Split out of :func:`rule_intent` because it answers a **narrower question that survives more
    rules**, and the gap is what makes editing an inherited redirect safe. ``rule_intent`` is
    ``None`` for any rule we could not have written *whole* — a 303, a target shape we cannot
    read — and every one of those still has a match set we understand perfectly well. Deciding
    "may the subdomain toggle be offered?" off the whole intent therefore hid the control on
    rules where it works, and — far worse — left an edit of one of them free to rebuild the
    expression from a checkbox nobody was shown, silently widening a live client's redirect from
    one hostname to every subdomain of it.

    ``None`` is the honest answer for Cloudflare's own ``http.host in {"klant.nl" "www.klant.nl"}``
    and for anything else we cannot reproduce: the rule is still editable (see :func:`edited_rule`),
    its match set is simply not ours to move.
    """
    return _host_forms(apex).get(_normalise_expression(str(rule.get("expression") or "")))


def rule_intent(rule: dict[str, Any], apex: str) -> dict[str, Any] | None:
    """The intent that would have produced this rule, or ``None`` if we cannot say.

    Keyed exactly like :class:`schemas.RedirectIntent`, so the caller validates it rather than
    re-typing it, and so it stores as JSONB unchanged.

    ``None`` is a real answer and the honest one: a rule whose expression or target we do not
    recognise is still shown on the panel, described by Cloudflare's own text, with no adopt
    button. Returning a half-guessed intent instead would put a button there that either refuses
    (`cloudflare_redirect_differs`) or — far worse — succeeds and claims a rule as something it
    is not, after which an ordinary save would rewrite a live client's redirect.

    The status code is passed through as Cloudflare gave it; whether it is one schakl can express
    is :data:`models.REDIRECT_STATUS_CODES`' business, and asking here would put a second copy of
    that list in the one file that must not have opinions about storage.
    """
    if str(rule.get("action") or "") != "redirect":
        return None
    include_subdomains = rule_scope(rule, apex)
    if include_subdomains is None:
        return None
    read = rule_target(rule)
    if read is None:
        return None
    target_url, preserve_path = read
    from_value = _from_value(rule)
    status_code = from_value.get("status_code")
    if not isinstance(status_code, int) or isinstance(status_code, bool):
        return None
    return {
        "target_url": target_url,
        "status_code": status_code,
        "preserve_path": preserve_path,
        "preserve_query": bool(from_value.get("preserve_query_string")),
        "include_subdomains": include_subdomains,
    }


def domain_wide_for(rule: dict[str, Any], apex: str) -> bool:
    """Does this rule redirect *this whole domain*, rather than some corner of it?

    The question a domain's status hangs on, so it is answered by whole-expression matching
    against a closed set of shapes and never by looking for the apex *somewhere in* the
    expression. A rule reading ``http.host eq "oud.klant.nl"``, or one with
    ``and http.request.uri.path eq "/aanbieding"`` bolted on, mentions the apex and does not
    redirect the domain; treating either as one would put "omleiding" on a record that serves a
    site perfectly well.

    The failure direction is therefore deliberate: an unrecognised shape is *not* domain-wide, so
    a rule we cannot read is listed and left to a human rather than acted on.
    """
    expression = _normalise_expression(str(rule.get("expression") or ""))
    if not expression or str(rule.get("action") or "") != "redirect":
        return False
    if expression in _host_forms(apex):
        return True
    members = _HOST_SET.match(expression)
    if members is None:
        return False
    hosts = {_unquote(value).lower().rstrip(".") for value in _QUOTED.findall(members.group(1))}
    return apex.lower().rstrip(".") in hosts


def edited_rule(
    live: dict[str, Any],
    *,
    apex: str,
    target_url: str,
    status_code: int,
    preserve_path: bool,
    preserve_query: bool,
    include_subdomains: bool,
) -> dict[str, Any] | None:
    """A rule that already exists at Cloudflare, rewritten to a new intent. ``None`` = cannot.

    This is what makes an *inherited* redirect editable rather than merely adoptable, and it is
    :func:`build_rule` with one refusal bolted on: **changing where a redirect sends traffic must
    never silently change which traffic it catches.**

    So the action half — target, status code, path and query — is rebuilt from the intent for
    every rule, and the ``expression`` is rebuilt **only where we recognise it as a shape of ours**
    (:func:`rule_scope`). Cloudflare's dashboard emits ``http.host in {"klant.nl" "www.klant.nl"}``
    for the commonest rule an agency inherits; we can read it and cannot write it, and rebuilding
    it from an intent would quietly re-scope a live client's redirect. Kept verbatim, the edit does
    exactly what the admin asked and nothing else — which is also why the panel hides the subdomain
    checkbox on such a rule instead of drawing one that would be ignored.

    Two more things are the rule's own and are carried over rather than asserted:

    * **The description.** Editing somebody's rule is not claiming it, and stamping
      :data:`RULE_DESCRIPTION` on it would make it read in Cloudflare's dashboard as one schakl
      created — the exact confusion ``find_our_rule`` exists to prevent. Ours already carries the
      marker, so keeping what is there is right in both directions.
    * **``enabled``.** A rule somebody disabled on purpose stays disabled; an edit is a change of
      destination, not a switch.

    ``None`` when the rule has no expression at all to keep and none of ours to put there — a
    body we cannot honestly build, which the service turns into a refusal rather than a guess.
    """
    fresh = build_rule(
        apex=apex,
        target_url=target_url,
        status_code=status_code,
        preserve_path=preserve_path,
        preserve_query=preserve_query,
        include_subdomains=include_subdomains,
    )
    if rule_scope(live, apex) is None:
        expression = str(live.get("expression") or "")
        if not expression:
            return None
        fresh["expression"] = expression
    fresh["description"] = str(live.get("description") or "") or RULE_DESCRIPTION
    if live.get("enabled") is False:
        fresh["enabled"] = False
    return fresh


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


def find_rule(ruleset: dict[str, Any] | None, rule_id: str | None) -> dict[str, Any] | None:
    """Any rule in a fetched ruleset, by id.

    The lookup an edit or a delete of an *inherited* rule needs: the caller is naming a row it
    was shown, and whether schakl happens to own that row is a different question, asked
    separately by whoever cares. Because the id comes from outside, the answer stays scoped to
    the ruleset that was actually fetched — a rule id that names nothing in it is ``None`` and
    becomes a 404, never a call made on the strength of a string somebody posted.
    """
    if not rule_id or not ruleset:
        return None
    for rule in ruleset.get("rules") or []:
        if isinstance(rule, dict) and rule.get("id") == rule_id:
            return rule
    return None


def find_our_rule(ruleset: dict[str, Any] | None, rule_id: str | None) -> dict[str, Any] | None:
    """Our rule inside a fetched ruleset — by stored id, never by description.

    Matching on the description would let a tenant's hand-written rule named ``schakl: …`` be
    adopted and then edited or deleted by us. The id is the only safe key; a lost id means the
    rule reads as ``missing`` and is recreated, which is recoverable, whereas deleting somebody
    else's rule is not.

    Mechanically :func:`find_rule` — the difference is the ``rule_id`` the caller passes, which
    here is *ours from the row* and there is *the one the user pointed at*. Kept as its own name
    because that distinction is the whole safety argument, and a reconcile calling something
    named ``find_rule`` would read as though it might touch anything in the ruleset.
    """
    return find_rule(ruleset, rule_id)


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
