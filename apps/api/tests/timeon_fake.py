"""A scriptable stand-in for the Timeon API.

Installed through ``app.integrations.timeon.client.set_transport``, which is the module's only
network seam. Unset, every call goes to ``api.timeon.nl``, so a test that forgets to install this
fails loudly on connect rather than quietly passing.

It stubs at the **transport**, not at the client and not at the service. A request made through
it travels the real token exchange, the real 411-avoiding ``Content-Length``, the real month
windowing, the real ``totalItems`` completeness assertion, the real retry ladder and the real
envelope check — which is exactly the layer the bugs live at. A fake one level higher would let a
short window, a missing token header or a ``success: false`` body through untouched.

It is **stateful**, because half of what a sync does is a sequence: read a window, write a row,
read it back, notice it changed. Canned responses cannot express "the hour the previous call
saved", and a test that cannot express that cannot catch a reconcile that pairs the wrong rows.

Four of the live API's behaviours are reproduced deliberately, because they are the ones the
client exists to survive (``client.py``'s numbered rules):

* the token exchange is a POST with **no body** whose absence of ``Content-Length`` is a 411;
* every response is HTTP **200**, including refusals, which carry ``success: false``;
* ``hour/save`` **replaces** — a field left out of the body is cleared, not kept;
* ``filter.deleted`` is accepted and **ignored**, so a deleted row is simply absent.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

ORG_ID = 2362
ORG_NAME = "breik."


def _ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "resultObject": result, "message": None})


def _refused(message: str) -> httpx.Response:
    """Timeon's refusal shape: **HTTP 200** with ``success: false`` (rule 7)."""
    return httpx.Response(200, json={"success": False, "message": message, "resultObject": None})


class FakeTimeon:
    """One Timeon organisation, held in memory."""

    def __init__(self) -> None:
        self.api_key = "test-key"
        self.users: list[dict[str, Any]] = []
        self.customers: list[dict[str, Any]] = []
        self.projects: list[dict[str, Any]] = []
        #: ``hourID -> row``. Live rows only; a deleted one leaves the dict, which is exactly
        #: how the real API behaves from a reader's point of view.
        self.hours: dict[int, dict[str, Any]] = {}
        self.deleted: dict[int, dict[str, Any]] = {}
        self._next_hour = 3_700_000
        #: Every request: ``(method, path, body)``.
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        #: Set to refuse the *next* request whose path contains the fragment.
        self.failures: list[tuple[str, httpx.Response]] = []
        #: Make ``hour/list`` under-report by this many rows, to exercise the completeness
        #: assertion — the guard that stops a short window being read as a batch of deletions.
        self.drop_rows = 0
        #: Refuse the first N token exchanges with a 401 on the API call, to exercise re-exchange.
        self.expire_token_after: int | None = None
        self._api_calls_since_token = 0
        self.tokens_issued = 0

    # --- scripting ----------------------------------------------------------------------- #
    def add_user(self, user_id: int, name: str, email: str, **extra: Any) -> dict[str, Any]:
        row = {"userID": user_id, "name": name, "email": email, "isActive": True, **extra}
        self.users.append(row)
        return row

    def add_customer(self, customer_id: int, name: str, number: str) -> dict[str, Any]:
        row = {"customerID": customer_id, "name": name, "customerNumber": number}
        self.customers.append(row)
        return row

    def add_project(
        self,
        project_id: int,
        customer_id: int,
        name: str,
        *,
        status_id: int = 1,
        billable: bool = True,
        budget_seconds: int | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "projectID": project_id,
            "customerID": customer_id,
            "name": name,
            "statusID": status_id,
            "defaultBillable": billable,
        }
        if budget_seconds is not None:
            row["budget"] = {"budget": budget_seconds}
        self.projects.append(row)
        return row

    def add_hour(
        self,
        *,
        user_id: int,
        day: str,
        seconds: int,
        from_seconds: int | None = 32400,
        remark: str = "",
        customer_id: int | None = None,
        project_id: int | None = None,
        billable: bool = True,
        approved: bool = False,
        invoice_id: int | None = None,
        hour_id: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        hid = hour_id if hour_id is not None else self._take_id()
        row = {
            "hourID": hid,
            "organisationID": ORG_ID,
            "userID": user_id,
            "customerID": customer_id,
            "projectID": project_id,
            "date": f"{day}T00:00:00",
            "seconds": seconds,
            "fromSeconds": from_seconds,
            "breakSeconds": None,
            "remark": remark,
            "billable": billable,
            "approved": approved,
            "approvedBy": None,
            "approvedOn": None,
            "invoiceID": invoice_id,
            "deleted": False,
            "createdOn": f"{day}T12:00:00",
            **extra,
        }
        self.hours[hid] = row
        return row

    def _take_id(self) -> int:
        self._next_hour += 1
        return self._next_hour

    # --- transport ----------------------------------------------------------------------- #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        path = url.path
        body: dict[str, Any] = {}
        if request.content:
            try:
                body = json.loads(request.content.decode())
            except ValueError:
                body = {}
        self.calls.append((request.method, path, body))

        for index, (fragment, response) in enumerate(self.failures):
            if fragment in path:
                self.failures.pop(index)
                return response

        if path == "/token":
            return self._token(url.query, request)

        if request.headers.get("Authorization") != f"Bearer tok-{self.tokens_issued}":
            return httpx.Response(401, json={"message": "unauthorised"})
        self._api_calls_since_token += 1
        if (
            self.expire_token_after is not None
            and self._api_calls_since_token > self.expire_token_after
        ):
            # The four-hour token lapsed mid-run. The client is expected to re-exchange once.
            self._api_calls_since_token = 0
            self.expire_token_after = None
            return httpx.Response(401, json={"message": "token expired"})

        handler = {
            "/api/organisation": self._organisation,
            "/api/user/search": self._user_search,
            "/api/customer/list": self._customer_list,
            "/api/project/list": self._project_list,
            "/api/hour/list": self._hour_list,
            "/api/hour/save": self._hour_save,
            "/api/hour/delete": self._hour_delete,
            "/api/hour/approve": self._hour_approve,
            "/api/hour/disapprove": self._hour_disapprove,
            "/api/project/create": self._project_create,
            "/api/project/save": self._project_save,
        }.get(path)
        if handler is None:
            return httpx.Response(404, json={"message": f"no route {path}"})
        return handler(body)

    def _token(self, query: str, request: httpx.Request) -> httpx.Response:
        # The live API answers **411** to a bodyless POST that omits Content-Length. Reproduced,
        # because the header is the single least obvious thing about this API and a fake that
        # tolerates its absence would let a regression through silently.
        if request.headers.get("Content-Length") is None:
            return httpx.Response(411, text="Length Required")
        params = parse_qs(query)
        if params.get("grant_type") != ["apitoken"]:
            return httpx.Response(200, json={"success": False, "errorMessage": "bad grant"})
        if params.get("token") != [self.api_key]:
            return httpx.Response(
                200, json={"success": False, "errorMessage": "Invalid API key"}
            )
        self.tokens_issued += 1
        self._api_calls_since_token = 0
        return httpx.Response(
            200,
            json={
                "success": True,
                "access_token": f"tok-{self.tokens_issued}",
                "expires_in": 14400,
                "refresh_token": None,
                "errorMessage": None,
            },
        )

    # --- endpoints ------------------------------------------------------------------------ #
    def _organisation(self, _body: dict[str, Any]) -> httpx.Response:
        return _ok(
            {
                "organisationID": ORG_ID,
                "name": ORG_NAME,
                "isActive": True,
                "fieldProject": True,
                "fieldRemark": True,
                "fieldBillable": True,
                "fieldTimer": True,
                "fieldDistance": True,
                "fieldCategory": False,
                "fieldInternalRemark": False,
            }
        )

    def _user_search(self, _body: dict[str, Any]) -> httpx.Response:
        return _ok(list(self.users))

    def _paged(self, rows: list[dict[str, Any]], body: dict[str, Any]) -> httpx.Response:
        size = int(body.get("pageSize") or 100)
        page = int(body.get("page") or 1)
        total_pages = max(1, -(-len(rows) // size))
        chunk = rows[(page - 1) * size : page * size]
        return _ok({"items": chunk, "nrPages": total_pages, "page": page})

    def _customer_list(self, body: dict[str, Any]) -> httpx.Response:
        return self._paged(list(self.customers), body)

    def _project_list(self, body: dict[str, Any]) -> httpx.Response:
        return self._paged(list(self.projects), body)

    def _hour_list(self, body: dict[str, Any]) -> httpx.Response:
        """Grouped by day, with a ``summary.totalItems`` the client checks against.

        ``filter.deleted`` is read and **ignored**, exactly as the live API does: asking for
        deleted rows answers the live ones. That is why absence is the only deletion signal a
        sync gets, and why this fake must not be more helpful than the thing it stands in for.
        """
        flt = body.get("filter") or {}
        rows = list(self.hours.values())
        if ids := flt.get("hourIDs"):
            wanted = {int(i) for i in ids}
            rows = [r for r in rows if r["hourID"] in wanted]
        else:
            if start := flt.get("from"):
                rows = [r for r in rows if r["date"][:10] >= str(start)[:10]]
            if end := flt.get("to"):
                rows = [r for r in rows if r["date"][:10] <= str(end)[:10]]
        rows.sort(key=lambda r: (r["date"], r["hourID"]))
        total = len(rows)
        if self.drop_rows:
            rows = rows[: max(0, total - self.drop_rows)]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(row["date"][:10], []).append(row)
        return _ok(
            {
                "groups": [
                    {"group": day, "hourList": items} for day, items in sorted(groups.items())
                ],
                "summary": {"totalItems": total, "totalSeconds": sum(r["seconds"] for r in rows)},
                "lastGroup": None,
                "nrPages": 1,
            }
        )

    def _hour_save(self, body: dict[str, Any]) -> httpx.Response:
        """Create or **replace**.

        Replace, not patch — a field the body omits is cleared. Measured on the live API against
        a row created for the purpose, and reproduced here because it is the property a push has
        to be built around.
        """
        hour_id = body.get("hourID")
        if hour_id is not None and int(hour_id) not in self.hours:
            return _refused("hour not found")
        if hour_id is None:
            hour_id = self._take_id()
        existing = self.hours.get(int(hour_id), {})
        row = {
            "hourID": int(hour_id),
            "organisationID": ORG_ID,
            "userID": body.get("userID"),
            "customerID": body.get("customerID"),
            "projectID": body.get("projectID"),
            "taskID": body.get("taskID"),
            "categoryID": body.get("categoryID"),
            "date": body.get("date") or f"{date.today().isoformat()}T00:00:00",
            "seconds": int(body.get("seconds") or 0),
            "fromSeconds": body.get("fromSeconds"),
            "breakSeconds": None,
            "remark": body.get("remark") or "",
            "internalRemark": body.get("internalRemark") or "",
            "billable": bool(body.get("billable")),
            "distance": body.get("distance"),
            "distanceCategoryID": body.get("distanceCategoryID"),
            "expenseCategoryID": body.get("expenseCategoryID"),
            "expenseValue": body.get("expenseValue"),
            "rateID": body.get("rateID"),
            "contactpersonID": body.get("contactpersonID"),
            # Approval and invoicing are Timeon's own state and survive a save.
            "approved": existing.get("approved", False),
            "approvedBy": existing.get("approvedBy"),
            "approvedOn": existing.get("approvedOn"),
            "invoiceID": existing.get("invoiceID"),
            "deleted": False,
            "createdOn": existing.get("createdOn") or "2026-01-01T00:00:00",
        }
        self.hours[int(hour_id)] = row
        return _ok(row)

    def _hour_delete(self, body: dict[str, Any]) -> httpx.Response:
        hour_id = int(body.get("hourID") or 0)
        row = self.hours.pop(hour_id, None)
        if row is None:
            return _refused("hour not found")
        row["deleted"] = True
        self.deleted[hour_id] = row
        return _ok({"hourID": hour_id, "deleted": True})

    def _approve(self, body: dict[str, Any], value: bool) -> httpx.Response:
        raw = body.get("hourIDs") or ""
        # A comma-separated **string**, the one place in this API a list is spelled that way.
        if not isinstance(raw, str):
            return _refused("hourIDs must be a string")
        for part in [p for p in raw.split(",") if p.strip()]:
            row = self.hours.get(int(part))
            if row is not None:
                row["approved"] = value
                row["approvedOn"] = "2026-08-16T09:00:00" if value else None
        return _ok({"ok": True})

    def _hour_approve(self, body: dict[str, Any]) -> httpx.Response:
        return self._approve(body, True)

    def _hour_disapprove(self, body: dict[str, Any]) -> httpx.Response:
        return self._approve(body, False)

    def _project_create(self, body: dict[str, Any]) -> httpx.Response:
        row = self.add_project(
            max([p["projectID"] for p in self.projects], default=2_100_000) + 1,
            int(body.get("customerID") or 0),
            body.get("name") or "",
            status_id=int(body.get("statusID") or 1),
            billable=bool(body.get("defaultBillable")),
        )
        return _ok(row)

    def _project_save(self, body: dict[str, Any]) -> httpx.Response:
        for row in self.projects:
            if row["projectID"] == body.get("projectID"):
                row.update({k: v for k, v in body.items() if v is not None})
                return _ok(row)
        return _refused("project not found")
