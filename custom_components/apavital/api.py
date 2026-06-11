"""Async client for the My Apavital (my.apavital.ro) JSON API.

Authentication is a JWT bearer token (valid ~365 days), obtained via the
interactive login in ``auth.py`` and replayed as ``Authorization: Bearer`` on
every call. An expired/revoked token yields 401 (or an HTML/redirect response),
surfaced as ``ApavitalAuthError`` so the coordinator can drive re-auth.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, FormData

from .const import API_BASE, USER_AGENT

_LOGGER = logging.getLogger(__name__)


class ApavitalError(Exception):
    """Base error."""


class ApavitalAuthError(ApavitalError):
    """Raised when the token is missing/expired/invalid."""


def _ctx(page_name: str, page_label: str) -> dict[str, Any]:
    return {"ctrAdmin": False, "ctrEmail": "", "pageName": page_name, "pageLabel": page_label}


class ApavitalApiClient:
    """Calls the My Apavital endpoints using a JWT bearer token."""

    def __init__(self, session: ClientSession, token: str) -> None:
        self._session = session
        self._token = token.strip()

    @property
    def token(self) -> str:
        return self._token

    def update_token(self, token: str) -> None:
        self._token = token.strip()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": USER_AGENT,
            "Referer": "https://my.apavital.ro/home",
        }
        if extra:
            headers.update(extra)
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{API_BASE}/{path.lstrip('/')}"
        try:
            async with self._session.request(
                method, url, allow_redirects=False, **kwargs
            ) as resp:
                return await self._parse(resp, url)
        except ClientError as err:
            raise ApavitalError(f"Network error calling {url}: {err}") from err

    @staticmethod
    async def _parse(resp: ClientResponse, url: str) -> Any:
        if resp.status in (301, 302, 303, 307, 308):
            raise ApavitalAuthError(f"Token rejected (redirect from {url})")
        if resp.status in (401, 403):
            raise ApavitalAuthError(f"Not authorized for {url} (HTTP {resp.status})")
        if resp.status == 404:
            return None
        if resp.status >= 400:
            raise ApavitalError(f"HTTP {resp.status} for {url}")
        if "json" not in resp.headers.get("Content-Type", ""):
            raise ApavitalAuthError(f"Non-JSON response from {url} (token likely expired)")
        return await resp.json(content_type=None)

    async def _get(self, path: str, page_name: str, page_label: str) -> Any:
        params = {"ctrAdmin": "false", "ctrEmail": "", "pageName": page_name, "pageLabel": page_label}
        return await self._request("GET", path, headers=self._headers(), params=params)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request(
            "POST", path, headers=self._headers({"Content-Type": "application/json"}), json=payload
        )

    async def _post_form(self, path: str, fields: dict[str, str]) -> Any:
        form = FormData()
        for key, value in fields.items():
            form.add_field(key, str(value))
        return await self._request("POST", path, headers=self._headers(), data=form)

    # -- Endpoints ------------------------------------------------------------

    async def async_get_locuri(self) -> list[dict[str, Any]]:
        return await self._get("locuriCons", "/istoric_consum", "Istoric Consum") or []

    async def async_get_sold(self) -> Any:
        return await self._post_json("sold", {"withReset": "0", **_ctx("/home", "Acasa")})

    async def async_get_unpaid(self) -> list[dict[str, Any]]:
        return await self._post_json("facturi_unpaid", _ctx("/evidenta_facturi", "Facturi")) or []

    async def async_get_index_history(self) -> list[dict[str, Any]]:
        return await self._get("index_history", "/istoric_citiri", "Istoric Citiri") or []

    async def async_get_usage(self, client_code: str) -> list[dict[str, Any]]:
        data = await self._post_form("get_usage", {"clientCode": client_code, "ctrAdmin": "false"})
        if isinstance(data, dict):
            return data.get("data") or []
        return data or []

    async def async_validate(self) -> list[dict[str, Any]]:
        """Confirm the token works; return the consumption places."""
        return await self.async_get_locuri()
