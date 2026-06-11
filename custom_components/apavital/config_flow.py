"""Config flow for Apavital (email/password + OTP -> JWT)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApavitalApiClient, ApavitalAuthError, ApavitalError
from .auth import ApavitalLoginClient
from .const import CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ApavitalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Email/password + OTP login that yields a long-lived JWT."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._password: str = ""
        self._methods: list[dict[str, Any]] = []
        self._method_type: str = ""

    @property
    def _login(self) -> ApavitalLoginClient:
        return ApavitalLoginClient(async_get_clientsession(self.hass))

    async def _finish(self, token: str) -> ConfigFlowResult:
        """Validate the token and create/update the entry."""
        await ApavitalApiClient(async_get_clientsession(self.hass), token).async_validate()
        await self.async_set_unique_id(self._email.lower())

        if self.source == "reauth":
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates={CONF_TOKEN: token, CONF_EMAIL: self._email},
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._email, data={CONF_TOKEN: token, CONF_EMAIL: self._email}
        )

    # -- Step 1: credentials --------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._password = user_input[CONF_PASSWORD]
            try:
                token, methods = await self._login.async_begin(self._email, self._password)
            except ApavitalAuthError:
                errors["base"] = "invalid_auth"
            except ApavitalError:
                errors["base"] = "cannot_connect"
            else:
                if token:  # account without 2FA
                    return await self._finish(token)
                self._methods = methods
                return await self.async_step_method()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )

    # -- Step 2: choose OTP channel ------------------------------------------

    async def async_step_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = {
            m["hash"]: f"{m.get('metoda_2fa')}: {m.get('value', '')}"
            for m in self._methods
            if m.get("hash")
        }

        if user_input is not None:
            chosen = user_input["method"]
            method = next((m for m in self._methods if m.get("hash") == chosen), None)
            if method is None:
                errors["base"] = "unknown"
            else:
                self._method_type = method.get("metoda_2fa", "")
                try:
                    await self._login.async_send_code(
                        self._email, self._password, chosen, self._method_type
                    )
                except ApavitalError:
                    errors["base"] = "cannot_connect"
                else:
                    return await self.async_step_otp()

        return self.async_show_form(
            step_id="method",
            data_schema=vol.Schema({vol.Required("method"): vol.In(options)}),
            errors=errors,
        )

    # -- Step 3: enter OTP ----------------------------------------------------

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            code = str(user_input["code"]).strip()
            try:
                token = await self._login.async_verify(
                    self._email, self._password, code, self._method_type
                )
            except ApavitalAuthError:
                errors["base"] = "invalid_code"
            except ApavitalError:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish(token)

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )

    # -- Re-auth (token expired ~yearly, or revoked) --------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._email = entry_data.get(CONF_EMAIL, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._password = user_input[CONF_PASSWORD]
            try:
                token, methods = await self._login.async_begin(self._email, self._password)
            except ApavitalAuthError:
                errors["base"] = "invalid_auth"
            except ApavitalError:
                errors["base"] = "cannot_connect"
            else:
                if token:
                    return await self._finish(token)
                self._methods = methods
                return await self.async_step_method()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=self._email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )
