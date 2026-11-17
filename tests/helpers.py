"""A stand-in for the SignalWire API, for tests.

Every record below is synthetic and shaped like the examples on the docs pages. No test
talks to a real space.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import httpx

from ledger.config import Settings

SPACE = "example"
PROJECT_ID = "project-for-tests"
API_TOKEN = "token-for-tests"
PERSONAL_TOKEN = "personal-token-for-tests"
BASE = f"https://{SPACE}.signalwire.com"

Handler = Callable[[httpx.Request], httpx.Response]


def settings(**overrides: str) -> Settings:
    values = {
        "space": SPACE,
        "project_id": PROJECT_ID,
        "api_token": API_TOKEN,
        "personal_access_token": PERSONAL_TOKEN,
    }
    values.update(overrides)
    return Settings(**values)


def basic(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


PROJECT_AUTH = basic(PROJECT_ID, API_TOKEN)
PERSONAL_AUTH = basic("", PERSONAL_TOKEN)


class FakeApi:
    """Routes requests by path and records each one."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = dict(routes or {})
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"error": "Not Found"})
        if callable(route):
            return route(request)
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def page(path: str, data: list[dict[str, Any]], next_query: str | None = None) -> dict[str, Any]:
    """A paged list response, with a ``next`` link when ``next_query`` is given."""
    links = {"self": f"{BASE}{path}?page_size=50", "first": f"{BASE}{path}?page_size=50"}
    if next_query:
        links["next"] = f"{BASE}{path}?{next_query}"
    return {"links": links, "data": data}


# -- synthetic records, one per documented shape ----------------------------------------

VOICE_LAML = {
    "id": "3f9a2c41-8d7e-4b6a-9c15-0e2d4f6a8b70",
    "from": "+12065550100",
    "to": "+12065550101",
    "source": "laml",
    "type": "laml_call",
    "url": None,
    "direction": "inbound",
    "status": "completed",
    "duration": 37,
    "duration_ms": 37000,
    "billing_ms": 60000,
    "charge": 0.0085,
    "charge_details": [{"description": "Inbound call", "charge": 0.0085}],
    "created_at": "2024-05-06T12:20:00Z",
    "parent_id": None,
    "audio_in_mos": "4.5",
}
VOICE_RELAY = {
    "id": "7c1d9e52-1111-4b6a-9c15-0e2d4f6a8b71",
    "from": "+12065550102",
    "to": "+12065550103",
    "source": "realtime_api",
    "type": "relay_pstn_call",
    "url": None,
    "direction": "outbound",
    "status": "failed",
    "duration": 0,
    "duration_ms": 0,
    "billing_ms": 0,
    "charge": 0,
    "charge_details": [],
    "created_at": "2024-05-06T09:00:00Z",
    "parent_id": None,
    "qos_metrics": None,
}
VOICE_FABRIC = {
    "id": "aa11bb22-2222-4b6a-9c15-0e2d4f6a8b72",
    "from": "+12065550104",
    "to": "+12065550105",
    "source": "realtime_api",
    "type": "fabric_subscriber_device_leg",
    "url": None,
    "direction": "inbound",
    "status": None,
    "charge": 0,
    "charge_details": [],
    "created_at": "2024-05-05T08:00:00Z",
}
VOICE_DELETED = {
    "id": "dd00ee11-3333-4b6a-9c15-0e2d4f6a8b73",
    "discarded_at": "2024-05-07T00:00:00Z",
    "created_at": "2024-05-04T08:00:00Z",
}
MESSAGE = {
    "id": "5a6b7c8d-4444-4b6a-9c15-0e2d4f6a8b74",
    "from": "+12065550106",
    "to": "+12065550107",
    "status": "undelivered",
    "direction": "outbound-api",
    "kind": "sms",
    "source": "laml",
    "type": "laml_message",
    "url": None,
    "number_of_segments": 2,
    "charge": 0.00415,
    "charge_details": [{"description": "Outbound SMS", "charge": 0.00415}],
    "created_at": "2024-05-06T10:30:00Z",
    "error_message": "Unreachable destination handset",
    "error_code": "30003",
}
FAX = {
    "id": "9e8d7c6b-5555-4b6a-9c15-0e2d4f6a8b75",
    "from": "+12065550108",
    "to": "+12065550109",
    "status": "completed",
    "direction": "outbound-api",
    "source": "laml",
    "type": "laml_call",
    "url": None,
    "remote_station": None,
    "charge": 0.03,
    "number_of_pages": 3,
    "quality": "fine",
    "charge_details": [],
    "created_at": "2024-05-06T11:00:00Z",
    "error_code": None,
    "error_message": None,
}
VIDEO = {
    "id": "1b2c3d4e-6666-4b6a-9c15-0e2d4f6a8b76",
    "source": "realtime_api",
    "type": "video_room_session",
    "url": None,
    "room_name": "standup",
    "status": "completed",
    "locked": False,
    "started_at": "2024-05-06T14:00:00Z",
    "ended_at": "2024-05-06T14:25:30Z",
    "charge": 0.12,
    "created_at": "2024-05-06T14:00:00Z",
    "charge_details": [],
}
CONFERENCE_CXML = {
    "id": "c0ffee00-7777-4b6a-9c15-0e2d4f6a8b77",
    "created_at": "2024-05-06T15:00:00Z",
    "project_id": PROJECT_ID,
    "region": "us",
    "name": "support-line",
    "status": "completed",
    "max_size": 10,
    "current_participants": 0,
    "updated_at": "2024-05-06T15:30:00Z",
    "type": "cxml_conference",
}

BALANCE = {
    "type": "balance",
    "balance_in_microdollars": 42_500_000,
    "current_balance": 42.5,
    "low_balance_threshold_in_microdollars": 10_000_000,
    "low_balance_threshold": 10.0,
    "auto_topup_enabled": True,
}
USAGE = {
    "type": "usage",
    "month": "2024-05",
    "total_in_microdollars": 12_340_000,
    "total_in_dollars": 12.34,
    "usage_by_kind": [
        {"kind": "sms_outbound", "description": "Outbound SMS", "amount_in_microdollars": 2_340_000, "amount_in_dollars": 2.34},
        {"kind": "voice_inbound", "description": "Inbound voice", "amount_in_microdollars": 10_000_000, "amount_in_dollars": 10.0},
    ],
    "projects": [
        {
            "id": PROJECT_ID,
            "name": "Main",
            "amount_in_microdollars": 12_340_000,
            "amount_in_dollars": 12.34,
            "usage_by_kind": [
                {"kind": "voice_inbound", "description": "Inbound voice", "amount_in_microdollars": 10_000_000, "amount_in_dollars": 10.0},
            ],
        }
    ],
}
STATEMENT = {
    "type": "billing_statement",
    "month": "2024-04",
    "summary": {
        "usage_in_microdollars": 20_000_000,
        "usage_in_dollars": 20.0,
        "carrier_fees_in_microdollars": 1_500_000,
        "carrier_fees_in_dollars": 1.5,
        "taxes_in_microdollars": 250_000,
        "taxes_in_dollars": 0.25,
        "adjustments": [
            {"kind": "balance_top_up", "description": "Top-up", "amount_in_microdollars": 50_000_000, "amount_in_dollars": 50.0},
        ],
    },
    "usage_by_kind": USAGE["usage_by_kind"],
    "projects": USAGE["projects"],
}
PERIODS = {
    "data": [
        {"type": "billing_statement_period", "month": "2024-05", "uri": "/api/space/billing_statement?month=2024-05", "formats": ["json", "csv"]},
        {"type": "billing_statement_period", "month": "2024-04", "uri": "/api/space/billing_statement?month=2024-04", "formats": ["json", "csv", "pdf"]},
    ]
}
ADJUSTMENT = {
    "type": "balance_adjustment",
    "id": "adj-0001",
    "kind": "auto_balance_top_up",
    "amount_in_microdollars": 25_000_000,
    "amount": 25.0,
    "created_at": "2024-05-02T03:04:05Z",
    "payment_method_last4": "4242",
}
SPACE_INFO = {
    "type": "space",
    "id": "space-0001",
    "name": "Example Co",
    "subdomain": SPACE,
    "verified": True,
    "trial": False,
    "created_at": "2023-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
}
PROFILE = {
    "type": "billing_profile",
    "address_line1": "1 Example Street",
    "address_line2": None,
    "address_city": "Springfield",
    "address_state": "IL",
    "address_zip": "62701",
    "address_country": "US",
    "company_name": "Example Co",
    "contact_name": "Pat Example",
    "contact_email": ["billing@example.com"],
    "contact_phone": None,
    "tax_id_number": None,
    "monthly_invoices": True,
    "tax_id_number_required": False,
    "monthly_invoices_locked": False,
}
CARD = {
    "type": "payment_method",
    "id": "pm-0001",
    "brand": "visa",
    "last4": "4242",
    "expires_month": 4,
    "expires_year": 2030,
    "country": "US",
    "auto_topup_source": True,
}
LOW_BALANCE = {
    "type": "low_balance_setting",
    "threshold_in_microdollars": 10_000_000,
    "threshold": 10.0,
    "send_email": True,
    "send_webhook": False,
    "webhook_url": None,
    "webhook_method": None,
    "send_topup": True,
    "topup_amount_in_microdollars": 25_000_000,
    "topup_amount": 25.0,
    "topup_payment_method_id": "pm-0001",
}
PROJECTS = page("/api/projects", [{"id": PROJECT_ID, "name": "Main", "subproject": False}])


def full_api() -> FakeApi:
    """Every endpoint the app reads, answering with one synthetic record each."""
    return FakeApi(
        {
            "/api/voice/logs": page("/api/voice/logs", [VOICE_LAML, VOICE_RELAY]),
            f"/api/voice/logs/{VOICE_LAML['id']}": VOICE_LAML,
            f"/api/voice/logs/{VOICE_LAML['id']}/events": {
                "data": [
                    {
                        "event_at": "2024-05-06T12:20:00Z",
                        "level": "info",
                        "name": "calling_call_initiated",
                        "details": {},
                        "project_id": PROJECT_ID,
                        "log_id": VOICE_LAML["id"],
                    }
                ]
            },
            "/api/messaging/logs": page("/api/messaging/logs", [MESSAGE]),
            "/api/fax/logs": page("/api/fax/logs", [FAX]),
            "/api/video/logs": page("/api/video/logs", [VIDEO]),
            "/api/logs/conferences": page("/api/logs/conferences", [CONFERENCE_CXML]),
            "/api/space": SPACE_INFO,
            "/api/space/balance": BALANCE,
            "/api/space/usage": USAGE,
            "/api/space/billing_statements": PERIODS,
            "/api/space/billing_statement": STATEMENT,
            "/api/space/payment_history": page("/api/space/payment_history", [ADJUSTMENT]),
            "/api/space/billing_profile": PROFILE,
            "/api/space/payment_methods": [CARD],
            "/api/space/low_balance_setting": LOW_BALANCE,
            "/api/projects": PROJECTS,
        }
    )
