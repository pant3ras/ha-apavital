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
    }
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApavitalApiClient, ApavitalAuthError, ApavitalError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ApavitalDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls all available Apavital account data on a schedule."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ApavitalApiClient
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.entry = entry
        self.client = client

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

        return {"places": places, "balance": balance, "unpaid": unpaid}


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
