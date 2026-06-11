"""Sensor platform for Apavital."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ApavitalConfigEntry
from .const import DOMAIN
from .coordinator import ApavitalDataCoordinator

_LOGGER = logging.getLogger(__name__)

CURRENCY_RON = "RON"

# Candidate keys for pulling a number out of loosely-typed responses.
_BALANCE_KEYS = ("sold", "SOLD", "value", "result", "suma", "sumaTotala", "total", "amount", "debit", "balance")
_INVOICE_VALUE_KEYS = ("VALOARE", "valoare", "REST_PLATA", "rest", "suma", "SUMA", "total", "TOTAL")


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    # Romanian number formats: "1.234,56" (dot=thousands, comma=decimal),
    # "0,00" (comma decimal), "25.456" (dot decimal, as used for meter index).
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return round(float(text), 3)
    except (TypeError, ValueError):
        return None


def _ro_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%d.%m.%Y").date()
    except ValueError:
        return None


def _ro_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _extract_number(raw: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(raw, (int, float)):
        return round(float(raw), 2)
    if isinstance(raw, str):
        return _to_float(raw)
    if isinstance(raw, dict):
        for key in keys:
            if key in raw and (num := _to_float(raw[key])) is not None:
                return num
    if isinstance(raw, list) and raw:
        return _extract_number(raw[0], keys)
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ApavitalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Apavital sensors from the coordinator snapshot."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    for code in (coordinator.data or {}).get("places", {}):
        entities.append(ApavitalMeterIndexSensor(coordinator, code))
        entities.append(ApavitalLastReadingSensor(coordinator, code))
        entities.append(ApavitalLastReadingDateSensor(coordinator, code))

    entities.append(ApavitalBalanceSensor(coordinator, entry.entry_id))
    entities.append(ApavitalUnpaidSensor(coordinator, entry.entry_id))

    async_add_entities(entities)


def _place_device(info: dict[str, Any], code: str) -> DeviceInfo:
    address = info.get("ADRESA")
    contract = info.get("CONTRACT")
    name = address or contract or f"Apavital {code}"
    return DeviceInfo(
        identifiers={(DOMAIN, f"place_{code}")},
        manufacturer="Apavital",
        name=f"Apavital {name}",
        model="Water meter",
    )


def _account_device(entry_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"account_{entry_id}")},
        manufacturer="Apavital",
        name="Apavital account",
        model="Account",
    )


class _PlaceBase(CoordinatorEntity[ApavitalDataCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ApavitalDataCoordinator, code: str) -> None:
        super().__init__(coordinator)
        self._code = code
        self._attr_device_info = _place_device(self._info(), code)

    def _place(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("places", {}).get(self._code, {})

    def _info(self) -> dict[str, Any]:
        return self._place().get("info", {})

    def _latest_usage(self) -> dict[str, Any]:
        usage = self._place().get("usage") or []
        best, best_dt = {}, None
        for row in usage:
            dt = _ro_datetime(row.get("TIME"))
            if dt is not None and (best_dt is None or dt > best_dt):
                best, best_dt = row, dt
        return best

    def _latest_reading(self) -> dict[str, Any]:
        readings = self._place().get("readings") or []
        best, best_dt = {}, None
        for row in readings:
            d = _ro_date(row.get("DATA"))
            if d is not None and (best_dt is None or d > best_dt):
                best, best_dt = row, d
        return best


class ApavitalMeterIndexSensor(_PlaceBase):
    """Current water-meter index (m³) — feeds the HA water dashboard."""

    _attr_translation_key = "meter_index"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:water"

    def __init__(self, coordinator: ApavitalDataCoordinator, code: str) -> None:
        super().__init__(coordinator, code)
        self._attr_unique_id = f"{code}_meter_index"

    @property
    def native_value(self) -> float | None:
        usage = self._latest_usage()
        if (val := _to_float(usage.get("INDEX_CIT"))) is not None:
            return val
        # Fall back to the latest official reading.
        return _to_float(self._latest_reading().get("INDEX_CITIT"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        usage = self._latest_usage()
        info = self._info()
        return {
            "meter_serial": usage.get("METERSERIAL") or self._latest_reading().get("SERIA"),
            "measured_at": usage.get("TIME"),
            "address": info.get("ADRESA"),
            "contract": info.get("CONTRACT"),
            "client_code": info.get("GRUPMAS_COD"),
        }


class ApavitalLastReadingSensor(_PlaceBase):
    """Latest official meter reading (m³)."""

    _attr_translation_key = "last_reading"
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:gauge"

    def __init__(self, coordinator: ApavitalDataCoordinator, code: str) -> None:
        super().__init__(coordinator, code)
        self._attr_unique_id = f"{code}_last_reading"

    @property
    def native_value(self) -> float | None:
        return _to_float(self._latest_reading().get("INDEX_CITIT"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self._latest_reading()
        return {
            "date": r.get("DATA"),
            "type": r.get("TIP_CITIRE"),
            "meter_serial": r.get("SERIA"),
        }


class ApavitalLastReadingDateSensor(_PlaceBase):
    """Date of the latest official meter reading."""

    _attr_translation_key = "last_reading_date"
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: ApavitalDataCoordinator, code: str) -> None:
        super().__init__(coordinator, code)
        self._attr_unique_id = f"{code}_last_reading_date"

    @property
    def native_value(self) -> date | None:
        return _ro_date(self._latest_reading().get("DATA"))


class _AccountBase(CoordinatorEntity[ApavitalDataCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ApavitalDataCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_device_info = _account_device(entry_id)


class ApavitalBalanceSensor(_AccountBase):
    """Account balance (RON)."""

    _attr_translation_key = "balance"
    _attr_native_unit_of_measurement = CURRENCY_RON
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:scale-balance"

    def __init__(self, coordinator: ApavitalDataCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_balance"

    @property
    def native_value(self) -> float | None:
        return _extract_number((self.coordinator.data or {}).get("balance"), _BALANCE_KEYS)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"raw": (self.coordinator.data or {}).get("balance")}


class ApavitalUnpaidSensor(_AccountBase):
    """Number of unpaid invoices (with total + list as attributes)."""

    _attr_translation_key = "unpaid_invoices"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:file-document-alert"

    def __init__(self, coordinator: ApavitalDataCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_unpaid_invoices"

    def _unpaid(self) -> list[dict[str, Any]]:
        data = (self.coordinator.data or {}).get("unpaid")
        return data if isinstance(data, list) else []

    @property
    def native_value(self) -> int:
        return len(self._unpaid())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        unpaid = self._unpaid()
        total = 0.0
        found = False
        for inv in unpaid:
            val = _extract_number(inv, _INVOICE_VALUE_KEYS)
            if val is not None:
                total += val
                found = True
        return {
            "total_due": round(total, 2) if found else None,
            "invoices": unpaid,
        }
