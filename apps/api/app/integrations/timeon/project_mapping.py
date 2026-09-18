"""Translating between a Timeon project and a schakl ``Project``. Business-licensed — see LICENSE.

The hour half of this integration had a mapping file from the start; the project half had a
*pairing* and nothing else, which is why ``projects_direction = push`` wrote nothing for a month
while the screen said "Project changes go to Timeon" (``docs/TIMEON.md`` §5a). This file is what
a project means on each side, in the neutral shape :mod:`mapping` already compares hours in.

**Four fields, because four is what both systems can say.** A name, whether it is still open,
whether its hours are billable by default, and its budget. Everything else on either side —
schakl's assignees, colour and description, Timeon's PO number, roles and address — has no
counterpart, is never compared, and is *carried* across a save rather than authored
(:func:`project_update_payload`), for the reason ``hour/save`` taught: Timeon's saves replace.

**None of the write shapes are in Timeon's OpenAPI document as anything but bare integers**, so
they were read out of Timeon's own web app instead, which is the one client known to work:

- ``unitType`` is ``1`` for hours and ``2`` for euros, and an hour budget's ``value`` is in
  **seconds** (its form multiplies by 3600 on the way out and divides on the way in).
- ``periodType`` is always ``0``: Timeon's own screen cannot make a budget that resets, so a
  schakl budget that does (``monthly`` / ``weekly`` / ``daily``) has nothing to become over
  there. It canonicalises to :data:`~mapping.UNRESOLVED` — *a difference no direction of sync
  could act on is not a difference* — and the run says so once, as a warning.
- A budget is its **own resource** (``/api/budget``), not a field of the project, so creating a
  project and giving it a budget are two calls and the second can fail alone.
- ``statusID`` is ``1`` open / ``2`` closed, and changing it has a narrow endpoint of its own
  (``PATCH /api/project/status``) — preferred over the wholesale save for the obvious reason.
- ``projectTypeID`` ``0`` is Timeon's "billable" *kind* of project (the others are non-billable,
  leave, special leave and sick). A schakl project that is merely non-billable **by default** is
  still created as kind ``0`` with ``defaultBillable: false``: schakl decides billability per
  entry (#284), and a kind that forbade a billable hour would refuse rows schakl holds.
"""

from __future__ import annotations

from typing import Any

from app.integrations.timeon.mapping import UNRESOLVED

#: What the sync compares on a project, and therefore what it may write.
PROJECT_FIELDS = ("name", "closed", "billable", "budget")

STATUS_OPEN = 1
STATUS_CLOSED = 2

UNIT_HOURS = 1
UNIT_MONEY = 2

#: Timeon's "billable" project kind. See the module docstring for why it is the only one sent.
PROJECT_TYPE_BILLABLE = 0

#: schakl statuses that mean "nobody books on this any more".
_CLOSED_STATUSES = frozenset({"completed", "archived"})

#: The keys ``ProjectUpdate`` accepts (Timeon's schema, ``additionalProperties: false``). A save
#: replaces, so everything here is carried over from the project as just read.
_UPDATE_KEYS = (
    "projectID",
    "customerID",
    "name",
    "remark",
    "projectNumber",
    "poNumber",
    "externID",
    "poValue",
    "statusID",
    "defaultBillable",
    "hideFromInvoice",
    "defaultCategoryID",
    "defaultContactpersonID",
    "defaultDistance",
    "roles",
    "dateFrom",
    "dateTo",
    "autoApprove",
    "internalRemark",
    "address",
    "postalCode",
    "city",
    "countryID",
    "projectTypeID",
)


#: The keys Timeon's ``Budget`` schema accepts, for the same reason.
_BUDGET_KEYS = (
    "budgetID",
    "organisationID",
    "periodType",
    "unitType",
    "visibility",
    "value",
    "useBillable",
    "useApproved",
    "useDistance",
    "useExpenses",
    "useProducts",
    "projectID",
    "taskID",
    "visualInHour",
    "canOverspent",
    "exclusionCategoryIDs",
    "exclusionCategoryGroupIDs",
)


def _status_value(project: Any) -> str:
    status = getattr(project, "status", None)
    return str(getattr(status, "value", status) or "")


def is_closed(project: Any) -> bool:
    return _status_value(project) in _CLOSED_STATUSES


def _hours_token(hours: float) -> str:
    return f"h:{round(float(hours), 2):.2f}"


def _money_token(amount: float) -> str:
    return f"e:{round(float(amount), 2):.2f}"


def local_budget(project: Any) -> str:
    """A schakl budget as one comparable token: ``h:84.00``, ``e:1500.00``, ``""`` or the sentinel.

    Hours win where a project carries both, because Timeon holds **one** budget per project and
    hours are what a time registration is about. Compared in *hours to two decimals* rather than
    seconds: the importer stored ``round(seconds / 3600, 2)``, so 303 600 s became 84.33 h, and
    comparing seconds would report every such budget as changed by twelve seconds forever.
    """
    hours = getattr(project, "budget_hours", None)
    amount = getattr(project, "budget_amount", None)
    if not hours and not amount:
        return ""
    if (getattr(project, "budget_period", None) or "total") != "total":
        return UNRESOLVED
    return _hours_token(hours) if hours else _money_token(amount)


def remote_budget(row: dict[str, Any]) -> str:
    """The same token, off a Timeon project row (``calculateBudget: true``)."""
    budget = row.get("budget") or {}
    value = budget.get("budget")
    if not value:
        return ""
    unit = budget.get("unitType") or UNIT_HOURS
    if unit == UNIT_HOURS:
        return _hours_token(float(value) / 3600.0)
    if unit == UNIT_MONEY:
        return _money_token(value)
    return UNRESOLVED


def neutral_from_project(project: Any) -> dict[str, Any]:
    return {
        "name": (getattr(project, "name", "") or "").strip(),
        "closed": is_closed(project),
        "billable": bool(getattr(project, "billable_default", True)),
        "budget": local_budget(project),
    }


def neutral_from_project_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": (row.get("name") or "").strip(),
        "closed": row.get("statusID") == STATUS_CLOSED,
        "billable": bool(row.get("defaultBillable")),
        "budget": remote_budget(row),
    }


def budget_values(token: str) -> dict[str, float | None]:
    """A remote budget token as the two schakl columns a pull writes.

    One budget over there means one here: taking over a euro budget clears the hour budget, or
    the project page would print a figure Timeon no longer holds beside the one it does.
    """
    if token.startswith("h:"):
        return {"budget_hours": float(token[2:]), "budget_amount": None}
    if token.startswith("e:"):
        return {"budget_hours": None, "budget_amount": float(token[2:])}
    return {"budget_hours": None, "budget_amount": None}


def project_create_payload(
    project: Any, *, customer_ext: str, project_number: str | None
) -> dict[str, Any]:
    """The body for ``project/create`` — what Timeon's own "new project" dialog sends, plus the
    fields schakl actually knows. ``externalID`` carries schakl's id, so the row over there says
    where it came from to anybody looking at it."""
    payload: dict[str, Any] = {
        "customerID": int(customer_ext),
        "name": (project.name or "").strip(),
        "statusID": STATUS_CLOSED if is_closed(project) else STATUS_OPEN,
        "defaultBillable": bool(project.billable_default),
        "projectTypeID": PROJECT_TYPE_BILLABLE,
        "externalID": str(project.id),
    }
    if project_number:
        payload["projectNumber"] = project_number
    if getattr(project, "start_date", None):
        payload["dateFrom"] = f"{project.start_date.isoformat()}T00:00:00"
    if getattr(project, "end_date", None):
        payload["dateTo"] = f"{project.end_date.isoformat()}T00:00:00"
    return payload


def project_update_payload(current: dict[str, Any], project: Any) -> dict[str, Any]:
    """The body for ``project/save``: the project **as just read**, with two fields of ours.

    Whole, because the save replaces (``client.py`` rule 3, one resource over). Status is not
    set here — it has its own endpoint — but it is *carried*, or a rename would reopen a closed
    project.
    """
    payload = {key: current[key] for key in _UPDATE_KEYS if current.get(key) is not None}
    # The read names it one way and the write the other.
    if "externID" not in payload and current.get("externalID") is not None:
        payload["externID"] = current["externalID"]
    payload["name"] = (project.name or "").strip()
    payload["defaultBillable"] = bool(project.billable_default)
    return payload


def budget_payload(
    project: Any,
    *,
    project_ext: str,
    organisation_id: int | None,
    current: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The body for ``POST /api/budget``, or ``None`` where schakl's budget cannot be said there.

    An existing budget is sent back whole with its two numbers changed, so the switches schakl
    has no concept of (count only approved hours, leaders-only visibility, the excluded
    categories) survive. A new one takes the defaults Timeon's own form opens on.
    """
    token = local_budget(project)
    if not token or token == UNRESOLVED:
        return None
    if token.startswith("h:"):
        unit, value = UNIT_HOURS, round(float(token[2:]) * 3600)
    else:
        unit, value = UNIT_MONEY, float(token[2:])
    base: dict[str, Any] = {
        key: (current or {})[key] for key in _BUDGET_KEYS if (current or {}).get(key) is not None
    }
    if not base.get("budgetID"):
        base = {
            "budgetID": 0,
            "taskID": None,
            "visibility": 0,
            "useApproved": False,
            "useBillable": False,
            "useDistance": False,
            "useExpenses": False,
            "useProducts": False,
            "visualInHour": False,
            "canOverspent": True,
        }
    base.update(
        {"projectID": int(project_ext), "periodType": 0, "unitType": unit, "value": value}
    )
    if organisation_id is not None:
        base.setdefault("organisationID", organisation_id)
    return base
