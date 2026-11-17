"""The space Billing and Balance APIs: statements, usage, payments and the billing profile.

Everything here is space-wide and is read with a Personal access token whose holder is an
owner or admin of the space. A project API token is not accepted on these endpoints.

Docs:
  https://signalwire.com/docs/apis/rest/space/billing/list-billing-statements
  https://signalwire.com/docs/apis/rest/space/billing/get-billing-statement
  https://signalwire.com/docs/apis/rest/space/billing/get-usage
  https://signalwire.com/docs/apis/rest/space/billing/list-payment-history
  https://signalwire.com/docs/apis/rest/space/billing/get-billing-profile
  https://signalwire.com/docs/apis/rest/space/billing/list-payment-methods
  https://signalwire.com/docs/apis/rest/space/balance/get-balance
  https://signalwire.com/docs/apis/rest/space/balance/get-low-balance-setting
  https://signalwire.com/docs/apis/rest/space/get-space
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .client import PERSONAL, ApiError, Download, Page, SignalWireClient
from .money import format_micro, share

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
STATEMENT_FILE_FORMATS = ("csv", "pdf")
TOP_UP_KINDS = {"balance_top_up", "auto_balance_top_up"}


def valid_month(value: str | None) -> str | None:
    """Return ``value`` when it is a calendar month written ``YYYY-MM``, else ``None``."""
    return value if value and _MONTH.match(value) else None


def month_bounds(month: str) -> tuple[date, date]:
    """The first and last day of a ``YYYY-MM`` month."""
    year, number = int(month[:4]), int(month[5:7])
    first = date(year, number, 1)
    following = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
    return first, date.fromordinal(following.toordinal() - 1)


# -- requests ---------------------------------------------------------------------------


async def space(client: SignalWireClient) -> dict[str, Any]:
    return await client.get("/api/space", credential=PERSONAL, ttl=300)


async def project_name(client: SignalWireClient, project_id: str) -> str | None:
    """The name of a project, looked up in the list of projects the token holder can reach.

    https://signalwire.com/docs/apis/rest/projects/list-projects
    """
    async for project in client.paginate("/api/projects", credential=PERSONAL, max_pages=5):
        if project.get("id") == project_id:
            return project.get("name")
    return None


async def balance(client: SignalWireClient) -> dict[str, Any]:
    return await client.get("/api/space/balance", credential=PERSONAL, ttl=30)


async def low_balance_setting(client: SignalWireClient) -> dict[str, Any]:
    return await client.get("/api/space/low_balance_setting", credential=PERSONAL, ttl=30)


async def usage(client: SignalWireClient, month: str | None = None) -> dict[str, Any]:
    """Usage for a month: the total, by kind and by project. Defaults to the current month."""
    return await client.get(
        "/api/space/usage", credential=PERSONAL, params={"month": month}, ttl=30
    )


async def statement_periods(client: SignalWireClient) -> list[dict[str, Any]]:
    """The months a statement is available for, newest first. This list is not paged."""
    body = await client.get("/api/space/billing_statements", credential=PERSONAL, ttl=300)
    return list(body.get("data") or []) if isinstance(body, dict) else []


async def statement(
    client: SignalWireClient,
    month: str,
    *,
    kinds: list[str] | None = None,
    projects: list[str] | None = None,
) -> dict[str, Any]:
    """One month's statement. ``kinds`` and ``projects`` filter the breakdown."""
    return await client.get(
        "/api/space/billing_statement",
        credential=PERSONAL,
        params={"month": month, "kind": kinds, "project": projects},
        ttl=30,
    )


async def statement_file(
    client: SignalWireClient,
    month: str,
    fmt: str,
    *,
    kinds: list[str] | None = None,
    projects: list[str] | None = None,
) -> Download:
    """The statement as a CSV or PDF file. A PDF exists only for a closed month."""
    if fmt not in STATEMENT_FILE_FORMATS:
        raise ApiError("A statement can be downloaded as CSV or PDF.")
    params: dict[str, Any] = {"month": month}
    if fmt == "csv":  # the PDF endpoint documents only `month`
        params.update(kind=kinds, project=projects)
    return await client.download(
        f"/api/space/billing_statement.{fmt}", credential=PERSONAL, params=params
    )


async def payment_history(
    client: SignalWireClient,
    *,
    created_after: str | None = None,
    created_before: str | None = None,
    cursor: str | None = None,
    page_size: int = 50,
) -> Page:
    """One page of balance adjustments: top-ups, auto top-ups, credits and debits."""
    return await client.get_page(
        "/api/space/payment_history",
        credential=PERSONAL,
        params={
            "created_after": created_after,
            "created_before": created_before,
            "page_size": page_size,
        },
        cursor=cursor,
    )


async def latest_top_up(client: SignalWireClient, since: date) -> dict[str, Any] | None:
    """The most recent top-up or auto top-up recorded since ``since``, if there is one."""
    found = [
        item
        async for item in client.paginate(
            "/api/space/payment_history",
            credential=PERSONAL,
            params={"created_after": since.isoformat(), "page_size": 50},
            max_pages=3,
        )
        if item.get("kind") in TOP_UP_KINDS
    ]
    # The docs do not state the order of the list, so pick the latest by its timestamp.
    return max(found, key=lambda item: str(item.get("created_at")), default=None)


async def billing_profile(client: SignalWireClient) -> dict[str, Any]:
    return await client.get("/api/space/billing_profile", credential=PERSONAL, ttl=60)


async def payment_methods(client: SignalWireClient) -> list[dict[str, Any]]:
    """The cards on file. The API answers with a bare array, not a paged envelope."""
    body = await client.get("/api/space/payment_methods", credential=PERSONAL, ttl=60)
    return list(body) if isinstance(body, list) else []


# -- shaping for display ----------------------------------------------------------------


def breakdown(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Amounts by kind (or by project), largest first, each with its share of the largest."""
    rows = []
    for item in items or []:
        micro = int(item.get("amount_in_microdollars") or 0)
        rows.append(
            {
                "kind": item.get("kind"),
                "id": item.get("id"),
                "label": item.get("description") or item.get("name") or item.get("kind")
                or ("Unnamed project" if item.get("id") else "Unnamed"),
                "micro": micro,
                # Two decimals, or four for an amount under one cent so it does not read as zero.
                "amount": format_micro(micro, 4 if 0 < abs(micro) < 10_000 else 2),
                "exact": format_micro(micro, 6),
                "usage_by_kind": item.get("usage_by_kind"),
            }
        )
    rows.sort(key=lambda r: r["micro"], reverse=True)
    top = max((r["micro"] for r in rows), default=0)
    for row in rows:
        row["pct"] = share(row["micro"], top)
        if row["usage_by_kind"] is not None:
            row["usage_by_kind"] = breakdown(row["usage_by_kind"])
    return rows


def statement_view(body: dict[str, Any]) -> dict[str, Any]:
    """A statement arranged for the page: summary lines, a total, and the two breakdowns."""
    summary = body.get("summary") or {}
    usage_micro = int(summary.get("usage_in_microdollars") or 0)
    fees_micro = int(summary.get("carrier_fees_in_microdollars") or 0)
    taxes_micro = int(summary.get("taxes_in_microdollars") or 0)
    return {
        "month": body.get("month"),
        "usage": format_micro(usage_micro),
        "carrier_fees": format_micro(fees_micro),
        "taxes": format_micro(taxes_micro),
        "total": format_micro(usage_micro + fees_micro + taxes_micro),
        "exact": {
            "usage": format_micro(usage_micro, 6),
            "carrier_fees": format_micro(fees_micro, 6),
            "taxes": format_micro(taxes_micro, 6),
            "total": format_micro(usage_micro + fees_micro + taxes_micro, 6),
        },
        "adjustments": breakdown(summary.get("adjustments")),
        "by_kind": breakdown(body.get("usage_by_kind")),
        "by_project": breakdown(body.get("projects")),
    }


def usage_view(body: dict[str, Any]) -> dict[str, Any]:
    total = int(body.get("total_in_microdollars") or 0)
    return {
        "month": body.get("month"),
        "total": format_micro(total),
        "by_kind": breakdown(body.get("usage_by_kind")),
        "by_project": breakdown(body.get("projects")),
    }


def adjustment_view(item: dict[str, Any]) -> dict[str, Any]:
    """One payment history entry. The amount keeps the sign it was recorded with."""
    micro = int(item.get("amount_in_microdollars") or 0)
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "label": str(item.get("kind") or "adjustment").replace("_", " ").capitalize(),
        "micro": micro,
        "amount": format_micro(micro),
        "created_at": item.get("created_at"),
        "last4": item.get("payment_method_last4"),
    }
