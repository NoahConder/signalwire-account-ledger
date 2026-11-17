"""A small, read-only HTTP client for the SignalWire REST API.

It knows the two credentials the documented endpoints ask for, how list endpoints page, and
how each error is shaped. It only ever sends ``GET``: a ledger reports, it does not change
the account.

Authentication: https://signalwire.com/docs/apis/authorization
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import (
    ENV_API_TOKEN,
    ENV_PERSONAL_ACCESS_TOKEN,
    ENV_PROJECT_ID,
    Settings,
)

# Which credential a request is signed with.
PROJECT = "project"  # project ID + API token: the Logs APIs
PERSONAL = "personal"  # Personal access token: everything under /api/space

Params = Mapping[str, Any]


class ApiError(Exception):
    """A request that did not succeed, with a message that is safe to show in the UI."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        credential: str | None = None,
        path: str = "",
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.credential = credential
        self.path = path
        self.errors = errors or []


class MissingCredential(ApiError):
    """The credential this request needs is not configured."""


@dataclass(frozen=True)
class Page:
    """One page of a list endpoint.

    ``next`` is the cursor for the following page, or ``None`` on the last page. Hand it
    back to :meth:`SignalWireClient.get_page` unchanged.
    """

    data: list[dict[str, Any]]
    next: str | None = None


@dataclass(frozen=True)
class Download:
    content: bytes
    content_type: str
    filename: str | None = None


def _clean(params: Params | None) -> dict[str, Any]:
    """Drop unset parameters. A list value is sent as a repeated parameter."""
    if not params:
        return {}
    return {k: v for k, v in params.items() if v not in (None, "", [])}


class SignalWireClient:
    """GET-only access to one space, signed with the right credential per request."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "signalwire-account-ledger"},
        )
        # Downloads redirected to file storage are fetched without any credential.
        self._plain = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._plain.aclose()

    # -- requests -----------------------------------------------------------------------

    async def get(
        self,
        path: str,
        *,
        credential: str,
        params: Params | None = None,
        scope: str | None = None,
        ttl: float = 0,
    ) -> Any:
        """GET ``path`` and return the parsed JSON body.

        ``scope`` names the API token scope the endpoint's docs page asks for; it is only
        used to make a permission error more helpful. ``ttl`` keeps the answer in memory
        for that many seconds, so one page view does not repeat a request.
        """
        query = _clean(params)
        key = (credential, path, tuple(sorted((k, str(v)) for k, v in query.items())))
        if ttl:
            hit = self._cache.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        response = await self._send(path, credential=credential, params=query, scope=scope)
        try:
            body = response.json()
        except ValueError as exc:
            raise ApiError(
                "SignalWire returned a response that is not JSON.",
                status=response.status_code,
                credential=credential,
                path=path,
            ) from exc
        if ttl:
            self._cache[key] = (time.monotonic() + ttl, body)
        return body

    async def get_page(
        self,
        path: str,
        *,
        credential: str,
        params: Params | None = None,
        cursor: str | None = None,
        scope: str | None = None,
    ) -> Page:
        """GET one page of a list endpoint that answers ``{"links": ..., "data": [...]}``.

        The first page is requested with ``params``. Every later page is requested with the
        ``cursor`` taken from the previous page's ``links.next``, which already carries the
        filters and the ``page_token``; the token is never built by hand.
        """
        if cursor:
            target = self._cursor_for(cursor, path)
            if target is None:
                raise ApiError("That page link does not belong to this list.", path=path)
            body = await self.get(target, credential=credential, scope=scope)
        else:
            body = await self.get(path, credential=credential, params=params, scope=scope)
        if not isinstance(body, dict):
            raise ApiError("SignalWire returned an unexpected list response.", path=path)
        links = body.get("links") or {}
        data = body.get("data") or []
        return Page(data=list(data), next=self._cursor_for(links.get("next"), path))

    async def paginate(
        self,
        path: str,
        *,
        credential: str,
        params: Params | None = None,
        scope: str | None = None,
        max_pages: int = 20,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every record of a list endpoint, following ``links.next`` page by page."""
        cursor: str | None = None
        for _ in range(max_pages):
            page = await self.get_page(
                path, credential=credential, params=params, cursor=cursor, scope=scope
            )
            for record in page.data:
                yield record
            cursor = page.next
            if not cursor:
                return

    async def download(
        self, path: str, *, credential: str, params: Params | None = None
    ) -> Download:
        """GET a file. A ``302`` is followed to its ``Location`` without credentials."""
        response = await self._send(path, credential=credential, params=_clean(params))
        if response.is_redirect:
            location = response.headers.get("location", "")
            if not location.startswith("https://"):
                raise ApiError("SignalWire redirected the download somewhere unexpected.", path=path)
            try:
                response = await self._plain.get(location)
            except httpx.HTTPError as exc:
                raise ApiError("The download link could not be reached.", path=path) from exc
            if response.status_code != 200:
                raise ApiError(
                    f"The download link answered {response.status_code}.",
                    status=response.status_code,
                    path=path,
                )
        match = re.search(r'filename="?([^";]+)', response.headers.get("content-disposition", ""))
        return Download(
            content=response.content,
            content_type=response.headers.get("content-type", "application/octet-stream"),
            filename=match.group(1) if match else None,
        )

    # -- internals ----------------------------------------------------------------------

    def _auth(self, credential: str) -> httpx.BasicAuth:
        s = self.settings
        if credential == PROJECT:
            if not s.has_project_credentials:
                raise MissingCredential(
                    f"Set {ENV_PROJECT_ID} and {ENV_API_TOKEN} to read this project's logs.",
                    credential=credential,
                )
            return httpx.BasicAuth(s.project_id, s.api_token)
        if credential == PERSONAL:
            if not s.has_personal_access_token:
                raise MissingCredential(
                    f"Set {ENV_PERSONAL_ACCESS_TOKEN} to read the space's billing. "
                    "A project API token is not accepted on these endpoints.",
                    credential=credential,
                )
            # A Personal access token is sent as the password, with an empty username.
            return httpx.BasicAuth("", s.personal_access_token)
        raise ValueError(f"unknown credential: {credential}")

    async def _send(
        self, path: str, *, credential: str, params: Params | None = None, scope: str | None = None
    ) -> httpx.Response:
        if not path.startswith("/api/"):
            raise ValueError(f"not an API path: {path}")
        auth = self._auth(credential)
        try:
            response = await self._http.get(path, params=params or None, auth=auth)
        except httpx.HTTPError as exc:
            raise ApiError(
                f"Could not reach {self.settings.host}. Check the space name and your connection.",
                credential=credential,
                path=path,
            ) from exc
        if response.status_code == 200 or response.is_redirect:
            return response
        raise self._error(response, credential=credential, path=path, scope=scope)

    def _cursor_for(self, link: str | None, path: str) -> str | None:
        """Turn a ``links.next`` URL into a cursor: its path and query, and nothing else.

        Only the path and query are kept, and only when the path is the list being read, so
        a cursor can never point the credentials at another host or another endpoint.
        """
        if not link:
            return None
        parts = urlsplit(link)
        if parts.path != path or not parts.query:
            return None
        if parts.netloc and parts.netloc.lower() != self.settings.host:
            return None
        return f"{parts.path}?{parts.query}"

    def _error(
        self, response: httpx.Response, *, credential: str, path: str, scope: str | None
    ) -> ApiError:
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        errors = [e for e in body.get("errors") or [] if isinstance(e, dict)]

        if status == 401:
            if isinstance(body.get("message"), str):
                # An unverified space: "Please validate a phone number to access your account."
                message = body["message"]
            elif credential == PERSONAL:
                message = (
                    "SignalWire did not accept the Personal access token. Its holder must be an "
                    "owner or admin of this space, and billing must be managed in the space itself."
                )
            else:
                message = "SignalWire did not accept the project ID and API token."
        elif status == 403:
            message = "Access is forbidden."
            if scope:
                message += f" Check that the API token has the {scope} scope."
        elif status == 404:
            message = "SignalWire has no record at that address."
        elif status == 422 and errors:
            parts = []
            for e in errors:
                text = str(e.get("message") or e.get("code") or "invalid parameter")
                if e.get("attribute"):
                    text = f"{e['attribute']}: {text}"
                parts.append(text)
            message = "; ".join(parts)
        elif status == 422:
            message = "The request contained an invalid parameter."
        elif status == 502:
            message = (
                "The billing or usage reporting service is unavailable right now. Try again shortly."
            )
        elif status >= 500:
            message = "SignalWire returned an internal server error. Try again shortly."
        else:
            message = f"SignalWire answered {status}."
            if isinstance(body.get("error"), str):
                message = f"{message[:-1]}: {body['error']}."
        return ApiError(message, status=status, credential=credential, path=path, errors=errors)
