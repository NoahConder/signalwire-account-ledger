"""The Logs APIs: voice, messaging, fax, video and conference history for one project.

Each product has its own endpoint and record shape. ``normalize`` maps every documented shape
onto one ledger row so the products can share a table, and keeps the original record beside it.

Docs:
  https://signalwire.com/docs/apis/rest/voice-logs/list-voice-logs
  https://signalwire.com/docs/apis/rest/message-logs/list-message-logs
  https://signalwire.com/docs/apis/rest/fax-logs/list-fax-logs
  https://signalwire.com/docs/apis/rest/video-logs/list-logs
  https://signalwire.com/docs/apis/rest/conference-logs/list-conferences
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from .client import PROJECT, ApiError, SignalWireClient
from .money import dollars_to_micro, format_micro

MAX_PAGE_SIZE = 1000  # the documented maximum for every log list
_LOG_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Product:
    key: str
    label: str
    path: str
    scope: str  # the API token scope the docs page asks for
    has_detail: bool = True  # a "get one log" endpoint is documented
    in_ledger: bool = True  # counted in the combined "All" view


PRODUCTS: dict[str, Product] = {
    p.key: p
    for p in (
        Product("voice", "Voice", "/api/voice/logs", "Voice"),
        Product("messaging", "Messages", "/api/messaging/logs", "Messaging"),
        Product("fax", "Fax", "/api/fax/logs", "Fax"),
        Product("video", "Video", "/api/video/logs", "Video"),
        # Conference logs list video room sessions too, with the same fields as the video
        # logs, and voice conferences carry no charge. They get their own tab and stay out
        # of the combined view so a session is not counted twice.
        Product(
            "conferences",
            "Conferences",
            "/api/logs/conferences",
            "Voice or Video",
            has_detail=False,
            in_ledger=False,
        ),
    )
}
LEDGER_PRODUCTS = [p.key for p in PRODUCTS.values() if p.in_ledger]


# -- time -------------------------------------------------------------------------------


def parse_time(value: object) -> datetime | None:
    """Parse an API timestamp (ISO 8601) into an aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def format_duration(seconds: float | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def window_params(start: date | None, end: date | None) -> dict[str, str]:
    """Date filters for a list request covering ``start`` to ``end``, both days included.

    The docs give ``created_after`` and ``created_before`` as dates but do not say whether
    the named day itself is included. So the request asks for one day more on each side,
    and ``in_window`` trims the result to the exact range. The answer is then the same
    whichever way the API reads the bounds.
    """
    params: dict[str, str] = {}
    if start:
        params["created_after"] = (start - timedelta(days=1)).isoformat()
    if end:
        params["created_before"] = (end + timedelta(days=1)).isoformat()
    return params


def in_window(moment: datetime | None, start: date | None, end: date | None) -> bool:
    """Whether ``moment`` falls on a day from ``start`` to ``end`` (UTC), both included."""
    if moment is None:
        return True
    if start and moment < datetime.combine(start, time.min, tzinfo=UTC):
        return False
    if end and moment >= datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC):
        return False
    return True


# -- one row shape for every product ----------------------------------------------------


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _span(record: dict[str, Any]) -> str | None:
    started, ended = parse_time(record.get("started_at")), parse_time(record.get("ended_at"))
    if started and ended:
        return format_duration((ended - started).total_seconds())
    return None


_TONES = {
    "good": {"completed", "delivered", "sent", "received", "answered", "ended"},
    "bad": {"failed", "undelivered", "busy", "no-answer", "canceled"},
    "active": {"queued", "initiated", "ringing", "in-progress", "created", "joined", "ending"},
}


def status_tone(status: object) -> str:
    """Group a status into good / bad / active / neutral, for the badge beside its label."""
    for tone, statuses in _TONES.items():
        if status in statuses:
            return tone
    return "neutral"


def normalize(product: str, record: dict[str, Any]) -> dict[str, Any]:
    """Map one log record onto the shared ledger row. Missing fields become ``None``."""
    info = PRODUCTS[product]
    charge_micro = dollars_to_micro(record.get("charge")) if "charge" in record else None
    row: dict[str, Any] = {
        "id": record.get("id"),
        "product": product,
        "product_label": info.label,
        "type": record.get("type"),
        "created_at": record.get("created_at"),
        "direction": record.get("direction"),
        "from": record.get("from"),
        "to": record.get("to"),
        "status": record.get("status"),
        "size": None,
        "charge_micro": charge_micro,
        "charge": format_micro(charge_micro, 4) if charge_micro is not None else None,
        "error": None,
        "deleted": False,
        "has_detail": info.has_detail,
        "record": record,
    }

    # A deleted log comes back with only id, discarded_at and created_at.
    if "discarded_at" in record:
        row.update(status="deleted", deleted=True, has_detail=False, tone="neutral")
        return row

    if product == "voice":
        row["size"] = format_duration(record.get("duration"))
    elif product == "messaging":
        parts = []
        if isinstance(record.get("number_of_segments"), int):
            parts.append(_plural(record["number_of_segments"], "segment"))
        if record.get("kind"):
            parts.append(str(record["kind"]).upper())
        row["size"] = " · ".join(parts) or None
    elif product == "fax":
        if isinstance(record.get("number_of_pages"), int):
            row["size"] = _plural(record["number_of_pages"], "page")
    elif product == "video":
        row["from"] = record.get("room_name")
        row["size"] = _span(record)
    elif product == "conferences":
        row["from"] = record.get("name") or record.get("room_name")
        if isinstance(record.get("current_participants"), int):
            row["size"] = _plural(record["current_participants"], "participant")
        else:
            row["size"] = _span(record)

    if record.get("error_code") or record.get("error_message"):
        row["error"] = " ".join(
            str(v) for v in (record.get("error_code"), record.get("error_message")) if v
        )
    row["tone"] = status_tone(row["status"])
    return row


def sort_key(row: dict[str, Any]) -> datetime:
    return parse_time(row.get("created_at")) or datetime.min.replace(tzinfo=UTC)


# -- requests ---------------------------------------------------------------------------


async def list_rows(
    client: SignalWireClient,
    product: str,
    *,
    start: date | None = None,
    end: date | None = None,
    cursor: str | None = None,
    page_size: int = 100,
    include_deleted: bool = False,
) -> dict[str, Any]:
    """One page of ledger rows for a product, trimmed to the date range.

    Returns ``{"rows": [...], "next": cursor or None}``. Pass ``next`` back as ``cursor``
    to read the following page.
    """
    info = PRODUCTS[product]
    params: dict[str, Any] = {
        **window_params(start, end),
        "page_size": max(1, min(page_size, MAX_PAGE_SIZE)),
        "include_deleted": "true" if include_deleted else None,
    }
    rows: list[dict[str, Any]] = []
    # A page can hold nothing but the extra day requested on either side of the range.
    # Read on a few pages instead of handing back an empty one with more behind it.
    for _ in range(5):
        page = await client.get_page(
            info.path, credential=PROJECT, params=params, cursor=cursor, scope=info.scope
        )
        rows = [
            normalize(product, record)
            for record in page.data
            if in_window(parse_time(record.get("created_at")), start, end)
        ]
        cursor = page.next
        if rows or not cursor:
            break
    return {"rows": rows, "next": cursor}


async def get_log(client: SignalWireClient, product: str, log_id: str) -> dict[str, Any]:
    """One log record by ID, as a ledger row."""
    info = PRODUCTS[product]
    if not info.has_detail:
        raise ApiError(f"{info.label} logs can only be listed.")
    if not _LOG_ID.match(log_id):
        raise ApiError("That is not a log ID.")
    record = await client.get(f"{info.path}/{log_id}", credential=PROJECT, scope=info.scope)
    return normalize(product, record)


async def voice_events(client: SignalWireClient, log_id: str) -> list[dict[str, Any]]:
    """The recorded event timeline of one voice log.

    https://signalwire.com/docs/apis/rest/voice-logs/list-voice-log-events
    """
    if not _LOG_ID.match(log_id):
        raise ApiError("That is not a log ID.")
    body = await client.get(
        f"{PRODUCTS['voice'].path}/{log_id}/events", credential=PROJECT, scope="Voice"
    )
    return list(body.get("data") or []) if isinstance(body, dict) else []


async def recent(client: SignalWireClient, limit: int = 10) -> dict[str, Any]:
    """The most recent rows across the ledger products, newest first.

    Returns ``{"rows": [...], "errors": {product_label: message}}``; a product that cannot
    be read is reported and the others still show.
    """

    async def one(product: str) -> tuple[str, list[dict[str, Any]] | ApiError]:
        try:
            return product, (await list_rows(client, product, page_size=limit))["rows"]
        except ApiError as exc:
            return product, exc

    rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for product, result in await asyncio.gather(*(one(p) for p in LEDGER_PRODUCTS)):
        if isinstance(result, ApiError):
            errors[PRODUCTS[product].label] = result.message
        else:
            rows.extend(result)
    rows.sort(key=sort_key, reverse=True)
    return {"rows": rows[:limit], "errors": errors}
