"""Tests for configuration, money, the HTTP client, logs and billing.

Run with ``pytest`` or ``python -m unittest``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest import mock

import httpx

from ledger import billing, config, logs
from ledger.client import PERSONAL, PROJECT, ApiError, MissingCredential, SignalWireClient
from ledger.config import ConfigError, Settings, load_settings, normalize_space
from ledger.money import dollars_to_micro, format_micro, share
from tests import helpers as h


class ConfigTests(unittest.TestCase):
    def test_space_name_is_normalized(self):
        self.assertEqual(normalize_space("Example"), "example")
        self.assertEqual(normalize_space(f"https://{h.SPACE}.signalwire.com/"), "example")
        with self.assertRaises(ConfigError):
            normalize_space("not a space")

    def test_settings_come_from_the_environment(self):
        s = load_settings({"SIGNALWIRE_SPACE": "example", "SIGNALWIRE_PROJECT_ID": "p"})
        self.assertEqual(s.base_url, h.BASE)
        self.assertFalse(s.has_project_credentials)  # the token is missing
        self.assertFalse(s.has_personal_access_token)
        with self.assertRaises(ConfigError):
            load_settings({})

    def test_an_empty_environment_variable_does_not_hide_the_env_file(self):
        # A container started before .env was filled in carries the variables with no value.
        lines = [f"{config.ENV_SPACE}=fromfile", f"{config.ENV_PROJECT_ID}=file-project"]
        process = {
            config.ENV_SPACE: "",
            config.ENV_PROJECT_ID: "from-env",
            config.ENV_API_TOKEN: "",
            config.ENV_PERSONAL_ACCESS_TOKEN: "",
        }
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, ".env").write_text("\n".join(lines) + "\n")
            previous = os.getcwd()
            os.chdir(folder)
            try:
                with mock.patch.dict(os.environ, process):
                    s = load_settings()
            finally:
                os.chdir(previous)
        self.assertEqual(s.space, "fromfile")
        self.assertEqual(s.project_id, "from-env")  # a variable with a value still wins

    def test_the_long_token_name_is_accepted_too(self):
        s = load_settings({config.ENV_SPACE: "example", "SIGNALWIRE_PERSONAL_ACCESS_TOKEN": "x"})
        self.assertTrue(s.has_personal_access_token)

    def test_tokens_never_appear_in_repr(self):
        text = repr(h.settings())
        self.assertNotIn(h.API_TOKEN, text)
        self.assertNotIn(h.PERSONAL_TOKEN, text)


class MoneyTests(unittest.TestCase):
    def test_dollars_become_exact_microdollars(self):
        self.assertEqual(dollars_to_micro(0.0085), 8500)
        self.assertEqual(dollars_to_micro("4.5"), 4_500_000)
        self.assertEqual(dollars_to_micro(0.1) + dollars_to_micro(0.2), 300_000)
        self.assertIsNone(dollars_to_micro(None))
        self.assertIsNone(dollars_to_micro("n/a"))

    def test_formatting(self):
        self.assertEqual(format_micro(1_234_500_000), "$1,234.50")
        self.assertEqual(format_micro(-8500, 4), "-$0.0085")
        self.assertEqual(format_micro(None), "—")

    def test_share(self):
        self.assertEqual(share(25, 100), 25.0)
        self.assertEqual(share(5, 0), 0.0)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = h.full_api()
        self.client = SignalWireClient(h.settings(), transport=self.api.transport)
        self.addAsyncCleanup(self.client.aclose)

    async def test_each_credential_is_sent_the_documented_way(self):
        await self.client.get("/api/voice/logs", credential=PROJECT)
        await self.client.get("/api/space/balance", credential=PERSONAL)
        logs_request, billing_request = self.api.requests
        self.assertEqual(logs_request.headers["authorization"], h.PROJECT_AUTH)
        # A Personal access token is the password of Basic auth with an empty username.
        self.assertEqual(billing_request.headers["authorization"], h.PERSONAL_AUTH)
        self.assertEqual(logs_request.url.host, f"{h.SPACE}.signalwire.com")

    async def test_a_missing_credential_sends_nothing(self):
        client = SignalWireClient(
            Settings(space=h.SPACE, project_id=h.PROJECT_ID, api_token=h.API_TOKEN),
            transport=self.api.transport,
        )
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(MissingCredential) as caught:
            await client.get("/api/space/balance", credential=PERSONAL)
        self.assertIn("SIGNALWIRE_PAT", caught.exception.message)
        self.assertEqual(self.api.requests, [])

    async def test_the_client_only_reads(self):
        for verb in ("post", "put", "patch", "delete"):
            self.assertFalse(hasattr(self.client, verb))
        with self.assertRaises(ValueError):
            await self.client.get("/not-the-api", credential=PROJECT)

    async def test_paging_follows_links_next_as_given(self):
        path = "/api/voice/logs"

        def route(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("page_token") == "tok-2":
                return httpx.Response(200, json=h.page(path, [h.VOICE_RELAY]))
            return httpx.Response(
                200,
                json=h.page(path, [h.VOICE_LAML], "page_number=1&page_size=1&page_token=tok-2&created_after=2024-05-01"),
            )

        self.api.routes[path] = route
        first = await self.client.get_page(path, credential=PROJECT, params={"page_size": 1})
        self.assertEqual([r["id"] for r in first.data], [h.VOICE_LAML["id"]])
        second = await self.client.get_page(path, credential=PROJECT, cursor=first.next)
        self.assertEqual([r["id"] for r in second.data], [h.VOICE_RELAY["id"]])
        self.assertIsNone(second.next)
        # The second request is the next link's own query: the token and the filters it carried.
        self.assertEqual(
            dict(self.api.requests[1].url.params),
            {"page_number": "1", "page_size": "1", "page_token": "tok-2", "created_after": "2024-05-01"},
        )

        seen = [r["id"] async for r in self.client.paginate(path, credential=PROJECT)]
        self.assertEqual(seen, [h.VOICE_LAML["id"], h.VOICE_RELAY["id"]])

    async def test_a_cursor_cannot_leave_its_list_or_the_space(self):
        with self.assertRaises(ApiError):
            await self.client.get_page(
                "/api/voice/logs", credential=PROJECT, cursor="/api/space/balance?x=1"
            )
        with self.assertRaises(ApiError):
            await self.client.get_page(
                "/api/voice/logs",
                credential=PROJECT,
                cursor="https://elsewhere.invalid/api/voice/logs?page_token=t",
            )
        self.assertEqual(self.api.requests, [])

    async def test_errors_become_readable_messages(self):
        cases = {
            "/api/a": (httpx.Response(401, text="Unauthorized"), PERSONAL, "Personal access token"),
            "/api/b": (
                httpx.Response(401, json={"message": "Please validate a phone number to access your account."}),
                PERSONAL,
                "validate a phone number",
            ),
            "/api/c": (
                httpx.Response(
                    422,
                    json={"errors": [{"type": "validation_error", "code": "invalid_month", "message": "Month is invalid", "attribute": "month", "url": ""}]},
                ),
                PERSONAL,
                "month: Month is invalid",
            ),
            "/api/d": (httpx.Response(502, content=b""), PERSONAL, "unavailable right now"),
            "/api/e": (httpx.Response(500, json={"error": "Internal Server Error"}), PROJECT, "internal server error"),
            "/api/f": (httpx.Response(403, json={"error": "Forbidden"}), PROJECT, "Video scope"),
            "/api/g": (httpx.Response(401, text="Unauthorized"), PROJECT, "project ID and API token"),
        }
        for path, (response, credential, expected) in cases.items():
            self.api.routes[path] = response
            with self.assertRaises(ApiError, msg=path) as caught:
                await self.client.get(path, credential=credential, scope="Video")
            self.assertIn(expected, caught.exception.message, path)
            self.assertEqual(caught.exception.status, response.status_code)

    async def test_an_unreachable_space_is_reported(self):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        self.api.routes["/api/space"] = boom
        with self.assertRaises(ApiError) as caught:
            await self.client.get("/api/space", credential=PERSONAL)
        self.assertIn("Could not reach", caught.exception.message)

    async def test_a_redirected_download_is_fetched_without_credentials(self):
        self.api.routes["/api/space/billing_statement.pdf"] = httpx.Response(
            302, headers={"location": "https://files.invalid/statement.pdf?sig=abc"}
        )
        self.api.routes["/statement.pdf"] = httpx.Response(
            200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
        )
        file = await self.client.download(
            "/api/space/billing_statement.pdf", credential=PERSONAL, params={"month": "2024-04"}
        )
        self.assertEqual(file.content, b"%PDF-1.7")
        self.assertEqual(file.content_type, "application/pdf")
        first, second = self.api.requests
        self.assertEqual(first.headers["authorization"], h.PERSONAL_AUTH)
        self.assertEqual(second.url.host, "files.invalid")
        self.assertNotIn("authorization", second.headers)

    async def test_ttl_keeps_an_answer_in_memory(self):
        await self.client.get("/api/space/balance", credential=PERSONAL, ttl=60)
        await self.client.get("/api/space/balance", credential=PERSONAL, ttl=60)
        self.assertEqual(len(self.api.requests), 1)


class LogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = h.full_api()
        self.client = SignalWireClient(h.settings(), transport=self.api.transport)
        self.addAsyncCleanup(self.client.aclose)

    def test_every_documented_shape_becomes_a_ledger_row(self):
        row = logs.normalize("voice", h.VOICE_LAML)
        self.assertEqual((row["from"], row["to"]), ("+12065550100", "+12065550101"))
        self.assertEqual((row["size"], row["charge_micro"], row["charge"]), ("37s", 8500, "$0.0085"))

        fabric = logs.normalize("voice", h.VOICE_FABRIC)  # no duration, status always null
        self.assertIsNone(fabric["size"])
        self.assertIsNone(fabric["status"])

        deleted = logs.normalize("voice", h.VOICE_DELETED)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["status"], "deleted")
        self.assertIsNone(deleted["charge_micro"])

        message = logs.normalize("messaging", h.MESSAGE)
        self.assertEqual(message["size"], "2 segments · SMS")
        self.assertEqual(message["error"], "30003 Unreachable destination handset")

        self.assertEqual(logs.normalize("fax", h.FAX)["size"], "3 pages")

        video = logs.normalize("video", h.VIDEO)
        self.assertEqual((video["from"], video["size"]), ("standup", "25m 30s"))
        self.assertEqual(video["charge_micro"], 120_000)

        conference = logs.normalize("conferences", h.CONFERENCE_CXML)
        self.assertEqual(conference["from"], "support-line")
        self.assertIsNone(conference["charge_micro"])  # voice conferences carry no charge
        self.assertFalse(conference["has_detail"])

    def test_durations(self):
        self.assertEqual(logs.format_duration(0), "0s")
        self.assertEqual(logs.format_duration(125), "2m 05s")
        self.assertEqual(logs.format_duration(3725), "1h 02m")
        self.assertIsNone(logs.format_duration(None))

    def test_the_date_window_is_exact_whichever_way_the_api_reads_its_bounds(self):
        start, end = date(2024, 5, 1), date(2024, 5, 31)
        self.assertEqual(
            logs.window_params(start, end),
            {"created_after": "2024-04-30", "created_before": "2024-06-01"},
        )
        at = lambda *a: datetime(*a, tzinfo=UTC)  # noqa: E731
        self.assertFalse(logs.in_window(at(2024, 4, 30, 23, 59, 59), start, end))
        self.assertTrue(logs.in_window(at(2024, 5, 1), start, end))
        self.assertTrue(logs.in_window(at(2024, 5, 31, 23, 59, 59), start, end))
        self.assertFalse(logs.in_window(at(2024, 6, 1), start, end))

    async def test_list_rows_trims_to_the_range_and_reads_past_an_empty_page(self):
        path = "/api/voice/logs"
        outside = {**h.VOICE_RELAY, "id": "outside", "created_at": "2024-06-01T00:00:01Z"}

        def route(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("page_token"):
                return httpx.Response(200, json=h.page(path, [h.VOICE_LAML]))
            return httpx.Response(200, json=h.page(path, [outside], "page_number=1&page_token=t"))

        self.api.routes[path] = route
        result = await logs.list_rows(
            self.client, "voice", start=date(2024, 5, 1), end=date(2024, 5, 31), include_deleted=True
        )
        self.assertEqual([r["id"] for r in result["rows"]], [h.VOICE_LAML["id"]])
        self.assertIsNone(result["next"])
        first = dict(self.api.requests[0].url.params)
        self.assertEqual(first["created_after"], "2024-04-30")
        self.assertEqual(first["created_before"], "2024-06-01")
        self.assertEqual(first["include_deleted"], "true")

    async def test_recent_merges_products_newest_first_and_reports_failures(self):
        self.api.routes["/api/fax/logs"] = httpx.Response(403, json={"error": "Forbidden"})
        result = await logs.recent(self.client, limit=3)
        self.assertEqual(
            [r["product"] for r in result["rows"]], ["video", "voice", "messaging"]
        )
        self.assertIn("Fax scope", result["errors"]["Fax"])
        self.assertNotIn("/api/logs/conferences", self.api.paths())  # not part of the ledger

    async def test_one_log_and_its_events(self):
        row = await logs.get_log(self.client, "voice", h.VOICE_LAML["id"])
        self.assertEqual(row["id"], h.VOICE_LAML["id"])
        events = await logs.voice_events(self.client, h.VOICE_LAML["id"])
        self.assertEqual(events[0]["name"], "calling_call_initiated")
        with self.assertRaises(ApiError):
            await logs.get_log(self.client, "voice", "../space/balance")
        with self.assertRaises(ApiError):
            await logs.get_log(self.client, "conferences", h.CONFERENCE_CXML["id"])


class BillingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = h.full_api()
        self.client = SignalWireClient(h.settings(), transport=self.api.transport)
        self.addAsyncCleanup(self.client.aclose)

    def test_months(self):
        self.assertEqual(billing.valid_month("2024-05"), "2024-05")
        self.assertIsNone(billing.valid_month("2024-13"))
        self.assertIsNone(billing.valid_month("May 2024"))
        self.assertEqual(billing.month_bounds("2024-12"), (date(2024, 12, 1), date(2024, 12, 31)))
        self.assertEqual(billing.month_bounds("2024-02")[1], date(2024, 2, 29))

    def test_breakdown_is_largest_first_with_shares(self):
        rows = billing.breakdown(h.USAGE["usage_by_kind"])
        self.assertEqual([r["kind"] for r in rows], ["voice_inbound", "sms_outbound"])
        self.assertEqual([r["pct"] for r in rows], [100.0, 23.4])
        self.assertEqual(rows[0]["amount"], "$10.00")
        tiny = billing.breakdown([{"kind": "k", "description": "Tiny", "amount_in_microdollars": 4500}])
        self.assertEqual(tiny[0]["amount"], "$0.0045")

    def test_statement_totals_are_summed_in_microdollars(self):
        view = billing.statement_view(h.STATEMENT)
        self.assertEqual((view["usage"], view["carrier_fees"], view["taxes"]), ("$20.00", "$1.50", "$0.25"))
        self.assertEqual(view["total"], "$21.75")
        self.assertEqual(view["adjustments"][0]["amount"], "$50.00")
        self.assertEqual(view["by_project"][0]["label"], "Main")
        self.assertEqual(view["by_project"][0]["usage_by_kind"][0]["kind"], "voice_inbound")

    def test_adjustments_keep_their_sign(self):
        debit = billing.adjustment_view({**h.ADJUSTMENT, "kind": "balance_debit_by_signalwire", "amount_in_microdollars": -5_000_000})
        self.assertEqual(debit["amount"], "-$5.00")
        self.assertEqual(debit["label"], "Balance debit by signalwire")

    async def test_statement_filters_are_repeated_parameters(self):
        await billing.statement(self.client, "2024-04", kinds=["a", "b"], projects=["p1"])
        request = self.api.requests[0]
        self.assertEqual(request.url.params.get_list("kind"), ["a", "b"])
        self.assertEqual(request.url.params.get_list("project"), ["p1"])
        self.assertEqual(request.url.params["month"], "2024-04")

    async def test_statement_files(self):
        self.api.routes["/api/space/billing_statement.csv"] = httpx.Response(
            200,
            text="kind,amount\n",
            headers={"content-type": "text/csv", "content-disposition": 'attachment; filename="billing_statement_2024-04.csv"'},
        )
        file = await billing.statement_file(self.client, "2024-04", "csv", kinds=["a"])
        self.assertEqual(file.filename, "billing_statement_2024-04.csv")
        self.assertEqual(self.api.requests[0].url.params.get_list("kind"), ["a"])
        with self.assertRaises(ApiError):
            await billing.statement_file(self.client, "2024-04", "xlsx")

    async def test_lists_and_lookups(self):
        self.assertEqual([p["month"] for p in await billing.statement_periods(self.client)], ["2024-05", "2024-04"])
        self.assertEqual((await billing.payment_methods(self.client))[0]["last4"], "4242")
        self.assertEqual(await billing.project_name(self.client, h.PROJECT_ID), "Main")
        self.assertIsNone(await billing.project_name(self.client, "unknown"))
        page = await billing.payment_history(self.client, created_after="2024-05-01")
        self.assertEqual(page.data[0]["id"], "adj-0001")
        for request in self.api.requests:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.headers["authorization"], h.PERSONAL_AUTH)


if __name__ == "__main__":
    unittest.main()
