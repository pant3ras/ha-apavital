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
# Invoice history rows: the face value, never what is left to pay.
_ISSUED_VALUE_KEYS = ("VALOARE", "valoare", "SOLD_INIT", "TOTAL", "total")
_PAID_VALUE_KEYS = ("TOTAL", "total", "SUMA", "suma", "VALOARE", "valoare")


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


def _any_date(value: Any) -> date | None:
    """Dates from the history endpoints: dd.mm.yyyy, with or without a time part."""
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _monthly_totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Sum rows shaped {"date": "YYYY-MM-DD", "amount": x} per calendar month."""
    totals: dict[str, float] = {}
    for row in rows:
        key = row["date"][:7]
        totals[key] = round(totals.get(key, 0.0) + row["amount"], 2)
    return dict(sorted(totals.items()))


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
        entities.append(ApavitalMonthlyConsumptionSensor(coordinator, code))

    entities.append(ApavitalBalanceSensor(coordinator, entry.entry_id))
    entities.append(ApavitalUnpaidSensor(coordinator, entry.entry_id))
    entities.append(ApavitalInvoicesSensor(coordinator, entry.entry_id))
    entities.append(ApavitalPaymentsSensor(coordinator, entry.entry_id))

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
            if not isinstance(row, dict):
                continue
            dt = _ro_datetime(row.get("TIME"))
            if dt is not None and (best_dt is None or dt > best_dt):
                best, best_dt = row, dt
        return best

    def _latest_reading(self) -> dict[str, Any]:
        readings = self._place().get("readings") or []
        best, best_dt = {}, None
        for row in readings:
            if not isinstance(row, dict):
                continue
            d = _ro_date(row.get("DATA"))
            if d is not None and (best_dt is None or d > best_dt):
                best, best_dt = row, d
        return best

    def _latest_monthly(self) -> dict[str, Any]:
        best, best_key = {}, None
        for row in self._place().get("monthly") or []:
            if not isinstance(row, dict):
                continue
            try:
                key = (int(row.get("AN")), int(row.get("LUNA")))
            except (TypeError, ValueError):
                continue
            if best_key is None or key > best_key:
                best, best_key = row, key
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
        history = []
        for row in self._place().get("readings") or []:
            if not isinstance(row, dict):
                continue
            read_on = _ro_date(row.get("DATA"))
            index = _to_float(row.get("INDEX_CITIT"))
            if read_on is None or index is None:
                continue
            history.append({"date": read_on.isoformat(), "index": index, "type": row.get("TIP_CITIRE")})
        history.sort(key=lambda h: h["date"])
        return {
            "date": r.get("DATA"),
            "type": r.get("TIP_CITIRE"),
            "meter_serial": r.get("SERIA"),
            "history": history,
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


class ApavitalMonthlyConsumptionSensor(_PlaceBase):
    """Most recent month's billed water consumption (m³)."""

    _attr_translation_key = "monthly_consumption"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator: ApavitalDataCoordinator, code: str) -> None:
        super().__init__(coordinator, code)
        self._attr_unique_id = f"{code}_monthly_consumption"

    @property
    def native_value(self) -> float | None:
        return _to_float(self._latest_monthly().get("CONSUM"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self._latest_monthly()
        # Every billed month the API returns, as {"YYYY-MM": m³}, for charts that
        # compare a month with the same month of the previous year.
        history: dict[str, float] = {}
        for row in self._place().get("monthly") or []:
            if not isinstance(row, dict):
                continue
            try:
                key = f"{int(row.get('AN')):04d}-{int(row.get('LUNA')):02d}"
            except (TypeError, ValueError):
                continue
            if (volume := _to_float(row.get("CONSUM"))) is not None:
                history[key] = round(history.get(key, 0.0) + volume, 3)
        return {"month": m.get("LUNA"), "year": m.get("AN"), "history": dict(sorted(history.items()))}


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


class ApavitalInvoicesSensor(_AccountBase):
    """Latest invoice (RON), with every invoice and per-month totals as attributes."""

    _attr_translation_key = "invoices"
    _attr_native_unit_of_measurement = CURRENCY_RON
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:file-document-multiple"

    def __init__(self, coordinator: ApavitalDataCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_invoices"

    def _rows(self) -> list[dict[str, Any]]:
        rows = []
        for inv in (self.coordinator.data or {}).get("invoices") or []:
            issued = _any_date(inv.get("DATA"))
            amount = _extract_number(inv, _ISSUED_VALUE_KEYS)
            if issued is None or amount is None:
                continue
            due = _any_date(inv.get("SCADENTA"))
            rows.append(
                {
                    "number": inv.get("FACTURA"),
                    "date": issued.isoformat(),
                    "due": due.isoformat() if due else None,
                    "amount": amount,
                }
            )
        return sorted(rows, key=lambda r: r["date"], reverse=True)

    @property
    def native_value(self) -> float | None:
        rows = self._rows()
        return rows[0]["amount"] if rows else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rows = self._rows()
        return {
            "last_invoice_date": rows[0]["date"] if rows else None,
            "invoices": rows,
            "monthly_totals": _monthly_totals(rows),
        }


class ApavitalPaymentsSensor(_AccountBase):
    """Latest payment (RON), with every payment and per-month totals as attributes."""

    _attr_translation_key = "payments"
    _attr_native_unit_of_measurement = CURRENCY_RON
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash-check"

    def __init__(self, coordinator: ApavitalDataCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_payments"

    def _rows(self) -> list[dict[str, Any]]:
        rows = []
        for pay in (self.coordinator.data or {}).get("payments") or []:
            paid_on = _any_date(pay.get("DATA"))
            amount = _extract_number(pay, _PAID_VALUE_KEYS)
            if paid_on is None or amount is None:
                continue
            rows.append(
                {
                    "date": paid_on.isoformat(),
                    "amount": amount,
                    "type": pay.get("NOTE"),
                    "document": pay.get("INCASARE"),
                }
            )
        return sorted(rows, key=lambda r: r["date"], reverse=True)

    @property
    def native_value(self) -> float | None:
        rows = self._rows()
        return rows[0]["amount"] if rows else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rows = self._rows()
        return {
            "last_payment_date": rows[0]["date"] if rows else None,
            "payments": rows,
            "monthly_totals": _monthly_totals(rows),
        }
