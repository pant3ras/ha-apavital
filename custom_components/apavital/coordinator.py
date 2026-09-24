"""Data update coordinator for Apavital.

One poll assembles, for every consumption place on the account:

    {
      "places": {
        <client_code>: {
          "info": {...},        # entry from locuriCons
          "usage": [...],       # smart-meter index/consumption (get_usage)
          "readings": [...],    # official readings (index_history_last_year)
          "monthly":  [...],    # monthly consumption (index_history)
        }
      },
      "balance": <raw sold response>,
      "unpaid": [...],          # facturi_unpaid
      "invoices": [...],        # facturi, every contract (best effort)
      "payments": [...],        # payments, every contract (best effort)
    }

Invoice and payment history change at most a few times a month, so they are
refreshed every HISTORY_INTERVAL rather than on every hourly poll, and a failure
there keeps the previous copy instead of failing the whole update.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ApavitalApiClient, ApavitalAuthError, ApavitalError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

HISTORY_INTERVAL = timedelta(hours=6)


class ApavitalDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls all available Apavital account data on a schedule."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ApavitalApiClient
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.entry = entry
        self.client = client
        self._history: dict[str, list[Any]] | None = None
        self._history_at: datetime | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch_all()
        except ApavitalAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ApavitalError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch_all(self) -> dict[str, Any]:
        places_raw = _as_list(await self.client.async_get_locuri())
        readings_all = _as_list(await self.client.async_get_readings())
        monthly_all = _as_list(await self.client.async_get_monthly())
        balance = await self.client.async_get_sold()
        unpaid = _as_list(await self.client.async_get_unpaid())

        places: dict[str, Any] = {}
        for place in places_raw:
            if not isinstance(place, dict):
                continue
            code = str(place.get("GRUPMAS_COD") or place.get("ID") or "")
            if not code:
                continue
            contrfurn = str(place.get("CONTRFURN_ID") or "")
            readings = [
                r
                for r in readings_all
                if isinstance(r, dict) and str(r.get("CONTRFURN_ID") or "") == contrfurn
            ]
            monthly = [
                m
                for m in monthly_all
                if isinstance(m, dict) and str(m.get("CONTRFURN_ID") or "") == contrfurn
            ]
            usage = _as_list(await self.client.async_get_usage(code))
            places[code] = {
                "info": place,
                "usage": usage,
                "readings": readings,
                "monthly": monthly,
            }

        history = await self._async_history()
        return {"places": places, "balance": balance, "unpaid": unpaid, **history}

    async def _async_history(self) -> dict[str, list[Any]]:
        """Invoice and payment history for every contract — best effort."""
        now = dt_util.utcnow()
        if self._history is not None and self._history_at and now - self._history_at < HISTORY_INTERVAL:
            return self._history
        invoices: list[Any] = []
        payments: list[Any] = []
        try:
            for contract in _as_list(await self.client.async_get_contracts()):
                if not isinstance(contract, dict):
                    continue
                code = contract.get("COD_CLIENT")
                place_id = contract.get("ID")
                if not code or place_id is None:
                    continue
                tag = {"_contract": str(code)}
                invoices += [
                    {**row, **tag}
                    for row in _as_list(await self.client.async_get_invoices(code, place_id))
                    if isinstance(row, dict)
                ]
                payments += [
                    {**row, **tag}
                    for row in _as_list(await self.client.async_get_payments(code, place_id))
                    if isinstance(row, dict)
                ]
        except ApavitalError as err:
            _LOGGER.debug("Apavital invoice/payment history unavailable: %s", err)
            if self._history is not None:
                return self._history
        self._history = {"invoices": invoices, "payments": payments}
        self._history_at = now
        return self._history


def _as_list(value: Any) -> list[Any]:
    """Coerce an API response into a list.

    Apavital endpoints usually return a bare JSON array, but depending on account
    state some wrap it in an object (e.g. ``{"data": [...]}``) or return a scalar.
    Normalising here keeps the rest of the coordinator simple and crash-free.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("data", "result", "results", "items", "rows", "list"):
            inner = value.get(key)
            if isinstance(inner, list):
                return inner
        _LOGGER.debug("Expected a list but got dict with keys %s", list(value))
    return []
