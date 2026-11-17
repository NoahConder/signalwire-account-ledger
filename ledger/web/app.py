"""The web app: five read-only pages over the Logs and Billing APIs.

The browser only ever talks to this app. The credentials stay in this process, and every
SignalWire request is a GET made by ``ledger.client``.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import billing, logs
from ..client import ApiError, SignalWireClient
from ..config import Settings, load_settings
from ..money import format_micro

HERE = Path(__file__).parent
DEFAULT_HOSTS = ["localhost", "127.0.0.1"]
ACTIVITY_PAGE_SIZE = 250  # rows per product for each "Load more"; the API allows up to 1000

# Only this app's own files, plus the three docs typefaces, may load in the page.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


# -- display helpers --------------------------------------------------------------------


def format_time(value: object) -> str:
    moment = logs.parse_time(value)
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else "—"


def format_day(value: object) -> str:
    moment = logs.parse_time(value)
    return f"{moment:%b} {moment.day}, {moment.year}" if moment else "—"


def month_label(month: str | None) -> str:
    if not billing.valid_month(month):
        return month or ""
    return f"{date(int(month[:4]), int(month[5:7]), 1):%B %Y}"


def current_month() -> str:
    return f"{datetime.now(UTC):%Y-%m}"


def recent_months(count: int = 12) -> list[str]:
    """The last ``count`` calendar months, newest first."""
    today = datetime.now(UTC).date()
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return months


async def attempt(call: Awaitable[Any]) -> tuple[Any, str | None]:
    """Await ``call``; return ``(value, None)`` or ``(None, message)``.

    Each block of a page is fetched through this, so one failing request (a missing
    credential, a scope, an outage) shows a notice in its own block and the rest still load.
    """
    try:
        return await call, None
    except ApiError as exc:
        return None, exc.message


# -- the app ----------------------------------------------------------------------------


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    client = SignalWireClient(settings, transport=transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="SignalWire account ledger", docs_url=None, redoc_url=None, lifespan=lifespan)

    # The app has no login of its own, so it answers only when addressed as localhost.
    hosts = allowed_hosts or [
        h.strip() for h in os.environ.get("LEDGER_ALLOWED_HOSTS", "").split(",") if h.strip()
    ]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or DEFAULT_HOSTS)

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters.update(
        time=format_time, day=format_day, month_label=month_label, money=format_micro
    )

    header_cache: dict[str, Any] = {}

    async def header() -> dict[str, Any]:
        """What the frame around every page shows: the space, the project and the months."""
        if header_cache.get("expires", 0) > time.monotonic():
            return header_cache["value"]
        (space, _), (periods, periods_error), (project, _) = await asyncio.gather(
            attempt(billing.space(client)),
            attempt(billing.statement_periods(client)),
            attempt(billing.project_name(client, settings.project_id)),
        )
        value = {
            "space_name": (space or {}).get("name") or settings.space,
            "trial": bool((space or {}).get("trial")),
            "project_label": project or (f"{settings.project_id[:8]}…" if settings.project_id else None),
            "periods": periods or [],
            "periods_error": periods_error,
            "months": [p["month"] for p in periods or [] if p.get("month")] or recent_months(),
            "has_project": settings.has_project_credentials,
            "has_personal": settings.has_personal_access_token,
        }
        header_cache.update(expires=time.monotonic() + 300, value=value)
        return value

    async def page(
        request: Request, template: str, active: str, month: str | None, **context: Any
    ) -> Response:
        head = await header()
        selected = billing.valid_month(month) or head["months"][0]
        months = head["months"] if selected in head["months"] else [selected, *head["months"]]
        return templates.TemplateResponse(
            request,
            template,
            {
                **head,
                "active": active,
                "month": selected,
                "months": months,
                "is_current_month": selected == current_month(),
                **context,
            },
        )

    def selected_month(month: str | None, head: dict[str, Any]) -> str:
        return billing.valid_month(month) or head["months"][0]

    # -- pages --------------------------------------------------------------------------

    @app.get("/")
    async def overview(request: Request, month: str | None = None) -> Response:
        head = await header()
        selected = selected_month(month, head)
        year_ago = (datetime.now(UTC) - timedelta(days=365)).date()
        (balance, balance_error), (usage, usage_error), (top_up, _), (recent, _) = (
            await asyncio.gather(
                attempt(billing.balance(client)),
                attempt(billing.usage(client, selected)),
                attempt(billing.latest_top_up(client, year_ago)),
                attempt(logs.recent(client, 10)),
            )
        )
        return await page(
            request,
            "overview.html",
            "overview",
            selected,
            balance=balance,
            balance_error=balance_error,
            usage=billing.usage_view(usage) if usage else None,
            usage_error=usage_error,
            top_up=billing.adjustment_view(top_up) if top_up else None,
            recent=recent or {"rows": [], "errors": {}},
        )

    @app.get("/activity")
    async def activity(
        request: Request, month: str | None = None, product: str = "all"
    ) -> Response:
        head = await header()
        selected = selected_month(month, head)
        first, last = billing.month_bounds(selected)
        return await page(
            request,
            "activity.html",
            "activity",
            selected,
            product=product if product in logs.PRODUCTS else "all",
            products=list(logs.PRODUCTS.values()),
            ledger_products=logs.LEDGER_PRODUCTS,
            date_from=first.isoformat(),
            date_to=last.isoformat(),
        )

    @app.get("/statements")
    async def statements(request: Request, month: str | None = None) -> Response:
        head = await header()
        selected = selected_month(month, head)
        body, error = await attempt(billing.statement(client, selected))
        period = next((p for p in head["periods"] if p.get("month") == selected), None)
        return await page(
            request,
            "statements.html",
            "statements",
            selected,
            statement=billing.statement_view(body) if body else None,
            statement_error=error,
            # A PDF exists only for a closed month; the list of periods says which formats do.
            formats=[f for f in (period or {}).get("formats", []) if f in billing.STATEMENT_FILE_FORMATS],
        )

    @app.get("/statements/{month}/statement.{fmt}")
    async def statement_download(request: Request, month: str, fmt: str) -> Response:
        if not billing.valid_month(month):
            return await page(request, "error.html", "statements", None, message="That is not a month.")
        file, error = await attempt(billing.statement_file(client, month, fmt))
        if error:
            return await page(request, "error.html", "statements", month, message=error)
        return Response(
            content=file.content,
            media_type=file.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="billing_statement_{month}.{fmt}"'
            },
        )

    @app.get("/payments")
    async def payments(
        request: Request,
        month: str | None = None,
        range_: str = Query("month", alias="range"),
        cursor: str | None = None,
    ) -> Response:
        head = await header()
        selected = selected_month(month, head)
        after = before = None
        if range_ != "all":
            # Payment history documents ISO 8601 timestamps for its date filters.
            first, last = billing.month_bounds(selected)
            after = f"{first.isoformat()}T00:00:00Z"
            before = f"{(last + timedelta(days=1)).isoformat()}T00:00:00Z"
        result, error = await attempt(
            billing.payment_history(client, created_after=after, created_before=before, cursor=cursor)
        )
        rows = [billing.adjustment_view(item) for item in result.data] if result else []
        return await page(
            request,
            "payments.html",
            "payments",
            selected,
            rows=rows,
            page_total=format_micro(sum(r["micro"] for r in rows)),
            next_cursor=result.next if result else None,
            is_first_page=not cursor,
            all_time=range_ == "all",
            payments_error=error,
        )

    @app.get("/account")
    async def account(request: Request, month: str | None = None) -> Response:
        (profile, profile_error), (cards, cards_error), (low, low_error) = await asyncio.gather(
            attempt(billing.billing_profile(client)),
            attempt(billing.payment_methods(client)),
            attempt(billing.low_balance_setting(client)),
        )
        return await page(
            request,
            "account.html",
            "account",
            month,
            profile=profile,
            profile_error=profile_error,
            cards=cards or [],
            cards_error=cards_error,
            low=low,
            low_error=low_error,
        )

    # -- data for the Activity table ----------------------------------------------------

    def parse_day(value: str | None) -> date | None:
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            return None

    @app.get("/api/activity/{product}")
    async def activity_rows(
        product: str,
        date_from: str | None = Query(None, alias="from"),
        date_to: str | None = Query(None, alias="to"),
        cursor: str | None = None,
        include_deleted: bool = False,
    ) -> JSONResponse:
        if product not in logs.PRODUCTS:
            return JSONResponse({"error": "Unknown product."}, status_code=404)
        try:
            result = await logs.list_rows(
                client,
                product,
                start=parse_day(date_from),
                end=parse_day(date_to),
                cursor=cursor,
                page_size=ACTIVITY_PAGE_SIZE,
                include_deleted=include_deleted,
            )
        except ApiError as exc:
            return JSONResponse({"error": exc.message}, status_code=502)
        return JSONResponse(result)

    @app.get("/api/activity/{product}/{log_id}")
    async def activity_detail(product: str, log_id: str) -> JSONResponse:
        if product not in logs.PRODUCTS:
            return JSONResponse({"error": "Unknown product."}, status_code=404)
        try:
            row = await logs.get_log(client, product, log_id)
        except ApiError as exc:
            return JSONResponse({"error": exc.message}, status_code=502)
        result: dict[str, Any] = {"row": row}
        if product == "voice" and not row["deleted"]:
            events, error = await attempt(logs.voice_events(client, log_id))
            result.update(events=events or [], events_error=error)
        return JSONResponse(result)

    return app
