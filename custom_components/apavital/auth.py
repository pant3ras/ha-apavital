"""Interactive login for My Apavital (username + password + OTP).

Apavital's login is a three-call dance against ``/api`` and yields a JWT that is
valid for ~365 days:

1. ``login_check`` with email+password -> the 2FA methods (masked email / phone),
   each with a ``hash`` identifying the channel. (Or a token directly if the
   account has no 2FA.)
2. ``create_code_anonym`` with the chosen method's hash -> sends the OTP.
3. ``login_check`` again with the OTP ``code`` -> returns ``{"token": <JWT>}``.

The JWT is then used as ``Authorization: Bearer`` for all data calls (no cookies).
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError, ClientSession

from .api import ApavitalAuthError, ApavitalError
from .const import API_BASE, LOGIN_LABEL, LOGIN_PAGE, USER_AGENT

_LOGGER = logging.getLogger(__name__)


class ApavitalLoginClient:
    """Drives the email/password + OTP login and returns a JWT."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": "https://my.apavital.ro/login",
        }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{API_BASE}/{path}"
        try:
            async with self._session.post(
                url, json=payload, headers=self._headers(), allow_redirects=False
            ) as resp:
                if resp.status >= 500:
                    raise ApavitalError(f"HTTP {resp.status} from {path}")
                if "json" not in resp.headers.get("Content-Type", ""):
                    raise ApavitalError(f"Unexpected non-JSON response from {path}")
                return await resp.json(content_type=None)
        except ClientError as err:
            raise ApavitalError(f"Network error during login: {err}") from err

    async def async_begin(
        self, email: str, password: str
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Submit credentials.

        Returns ``(token, methods)``: a token if the account needs no 2FA, else
        ``methods`` is the list of 2FA channels to choose from.
        """
        data = await self._post(
            "login_check",
            {
                "email": email,
                "password": password,
                "wtf": "1",
                "pageName": LOGIN_PAGE,
                "pageLabel": LOGIN_LABEL,
            },
        )
        if data.get("token"):
            return data["token"], []
        methods = data.get("methodes2FA")
        if isinstance(methods, list) and methods:
            return None, methods
        raise ApavitalAuthError("Invalid e-mail or password")

    async def async_send_code(
        self, email: str, password: str, method_hash: str, method_type: str
    ) -> None:
        """Ask Apavital to send the OTP via the chosen channel."""
        data = await self._post(
            "create_code_anonym",
            {
                "email": email,
                "password": password,
                "value": method_hash,
                "metoda_2fa": method_type,
                "pageName": LOGIN_PAGE,
                "pageLabel": LOGIN_LABEL,
            },
        )
        if not data.get("success"):
            raise ApavitalError(f"Could not send OTP: {data.get('msg') or data}")

    async def async_verify(
        self, email: str, password: str, code: str, method_type: str
    ) -> str:
        """Submit the OTP code and return the JWT."""
        data = await self._post(
            "login_check",
            {
                "email": email,
                "password": password,
                "value": None,
                "code": code,
                "metoda_2fa": method_type,
                "wtf": "1",
                "pageName": LOGIN_PAGE,
                "pageLabel": LOGIN_LABEL,
            },
        )
        token = data.get("token")
        if not token:
            raise ApavitalAuthError("Incorrect or expired OTP code")
        return token
