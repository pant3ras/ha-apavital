"""The Apavital integration.

Author: PanTeraS
"""

from __future__ import annotations

__author__ = "PanTeraS"

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApavitalApiClient
from .const import CONF_TOKEN
from .coordinator import ApavitalDataCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type ApavitalConfigEntry = ConfigEntry[ApavitalDataCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ApavitalConfigEntry) -> bool:
    """Set up Apavital from a config entry."""
    client = ApavitalApiClient(async_get_clientsession(hass), entry.data[CONF_TOKEN])
    coordinator = ApavitalDataCoordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ApavitalConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
