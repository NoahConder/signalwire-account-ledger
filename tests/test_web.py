"""Tests for the pages, served against the synthetic API in ``tests.helpers``."""

from __future__ import annotations

import unittest

import httpx
from fastapi.testclient import TestClient

from ledger.config import Settings
from ledger.web import app as web
from tests import helpers as h


def browser(api: h.FakeApi, settings: Settings | None = None) -> TestClient:
    app = web.create_app(settings or h.settings(), transport=api.transport, allowed_hosts=["testserver"])
    return TestClient(app)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.api = h.full_api()
        self.browser = browser(self.api)

    def test_overview_shows_balance_usage_and_recent_activity(self):
        page = self.browser.get("/?month=2024-05")
        self.assertEqual(page.status_code, 200)
        for expected in (
            "Example Co",  # the space name, from /api/space
            "$42.50",  # balance
            "$12.34",  # usage for the month
            "$25.00",  # latest top-up
            "card ending 4242",
            "Inbound voice",
            "Project Main",
            "+12065550100",
            "standup",
            "April 2024",
        ):
            self.assertIn(expected, page.text, expected)

    def test_statement_page_and_formats(self):
        closed = self.browser.get("/statements?month=2024-04")
        self.assertIn("$21.75", closed.text)
        self.assertIn("/statements/2024-04/statement.pdf", closed.text)
        self.assertIn("/statements/2024-04/statement.csv", closed.text)
        # The current month is offered as CSV only: its period lists no pdf format.
        current = self.browser.get("/statements?month=2024-05")
        self.assertIn("/statements/2024-05/statement.csv", current.text)
        self.assertNotIn("statement.pdf", current.text)
        self.assertIn("has not closed", current.text)

    def test_statement_download(self):
        self.api.routes["/api/space/billing_statement.csv"] = httpx.Response(
            200, text="kind,amount\n", headers={"content-type": "text/csv"}
        )
        file = self.browser.get("/statements/2024-04/statement.csv")
        self.assertEqual(file.text, "kind,amount\n")
        self.assertIn("billing_statement_2024-04.csv", file.headers["content-disposition"])
        self.assertIn("not a month", self.browser.get("/statements/nope/statement.csv").text)

    def test_payments_and_account(self):
        payments = self.browser.get("/payments?month=2024-05")
        self.assertIn("Auto balance top up", payments.text)
        self.assertIn("ending 4242", payments.text)
        sent = [r for r in self.api.requests if r.url.path == "/api/space/payment_history"][-1]
        self.assertEqual(sent.url.params["created_after"], "2024-05-01T00:00:00Z")
        self.assertEqual(sent.url.params["created_before"], "2024-06-01T00:00:00Z")

        self.browser.get("/payments?month=2024-05&range=all")
        sent = [r for r in self.api.requests if r.url.path == "/api/space/payment_history"][-1]
        self.assertNotIn("created_after", sent.url.params)

        account = self.browser.get("/account")
        for expected in ("1 Example Street", "billing@example.com", "Visa", "expires 04/2030", "$10.00"):
            self.assertIn(expected, account.text, expected)

    def test_activity_rows_and_detail(self):
        self.assertEqual(self.browser.get("/activity?product=fax").status_code, 200)
        body = self.browser.get("/api/activity/voice?from=2024-05-01&to=2024-05-31").json()
        self.assertEqual([r["id"] for r in body["rows"]], [h.VOICE_LAML["id"], h.VOICE_RELAY["id"]])
        self.assertEqual(body["rows"][0]["tone"], "good")
        self.assertEqual(body["rows"][1]["tone"], "bad")

        detail = self.browser.get(f"/api/activity/voice/{h.VOICE_LAML['id']}").json()
        self.assertEqual(detail["row"]["charge"], "$0.0085")
        self.assertEqual(detail["events"][0]["name"], "calling_call_initiated")

        self.assertEqual(self.browser.get("/api/activity/nope").status_code, 404)
        failed = self.browser.get("/api/activity/voice/not-there")
        self.assertEqual(failed.status_code, 502)
        self.assertIn("no record", failed.json()["error"])

    def test_every_upstream_request_is_a_get_to_the_configured_space(self):
        for path in ("/", "/activity", "/statements", "/payments", "/account", "/api/activity/video"):
            self.browser.get(path)
        self.assertTrue(self.api.requests)
        for request in self.api.requests:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.host, f"{h.SPACE}.signalwire.com")

    def test_credentials_never_reach_the_browser(self):
        for path in ("/", "/activity", "/statements", "/payments", "/account"):
            text = self.browser.get(path).text
            self.assertNotIn(h.API_TOKEN, text)
            self.assertNotIn(h.PERSONAL_TOKEN, text)

    def test_responses_are_private_and_only_localhost_is_served(self):
        page = self.browser.get("/")
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])
        app = web.create_app(h.settings(), transport=self.api.transport)
        other = TestClient(app, base_url="http://ledger.example.org")
        self.assertEqual(other.get("/").status_code, 400)


class FailureTests(unittest.TestCase):
    def test_a_missing_personal_token_leaves_the_logs_working(self):
        api = h.full_api()
        settings = Settings(space=h.SPACE, project_id=h.PROJECT_ID, api_token=h.API_TOKEN)
        page = browser(api, settings).get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("SIGNALWIRE_PAT", page.text)
        self.assertIn("+12065550100", page.text)  # recent activity still loads
        self.assertFalse([p for p in api.paths() if p.startswith("/api/space")])

    def test_a_missing_project_token_leaves_billing_working(self):
        api = h.full_api()
        settings = Settings(space=h.SPACE, personal_access_token=h.PERSONAL_TOKEN)
        client = browser(api, settings)
        page = client.get("/")
        self.assertIn("$42.50", page.text)
        self.assertIn("SIGNALWIRE_PROJECT_ID", page.text)
        self.assertIn("SIGNALWIRE_API_TOKEN", client.get("/api/activity/voice").json()["error"])

    def test_one_failing_block_does_not_take_the_page_down(self):
        api = h.full_api()
        api.routes["/api/space/usage"] = httpx.Response(502, content=b"")
        api.routes["/api/space/billing_statement"] = httpx.Response(
            422,
            json={"errors": [{"type": "validation_error", "code": "month_is_in_the_future", "message": "Month is in the future", "attribute": "month", "url": ""}]},
        )
        client = browser(api)
        overview = client.get("/")
        self.assertIn("unavailable right now", overview.text)
        self.assertIn("$42.50", overview.text)
        self.assertIn("month: Month is in the future", client.get("/statements?month=2024-05").text)


if __name__ == "__main__":
    unittest.main()
