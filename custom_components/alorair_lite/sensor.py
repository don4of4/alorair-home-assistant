"""Measured inlet/outlet values and clearly named diagnostics."""

import time
from datetime import UTC

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature, UnitOfTime

from .entity import AlorairEntity
from .models import code, faults, number, vendor_datetime

MEASUREMENTS = (
    ("inHumidity", "Inlet humidity", PERCENTAGE, SensorDeviceClass.HUMIDITY, 0, 100),
    ("outHumidity", "Outlet humidity", PERCENTAGE, SensorDeviceClass.HUMIDITY, 0, 100),
    ("inCelsius", "Inlet temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, -50, 100),
    ("outCelsius", "Outlet temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, -50, 100),
    ("inGkg", "Inlet specific humidity", "g/kg", None, 0, 1000),
    ("outGkg", "Outlet specific humidity", "g/kg", None, 0, 1000),
    ("inGrlb", "Inlet grains", "gr/lb", None, 0, 10000),
    ("outGrlb", "Outlet grains", "gr/lb", None, 0, 10000),
    ("singleWorktime", "Reported working time", UnitOfTime.HOURS, SensorDeviceClass.DURATION, 0, 10000000),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(AlorairMeasurement(coordinator, *item) for item in MEASUREMENTS),
            AlorairCoil(coordinator),
            AlorairFault(coordinator),
            AlorairSampleTime(coordinator),
            AlorairCommandFeedback(coordinator),
        ]
    )


class AlorairMeasurement(AlorairEntity, SensorEntity):
    def __init__(self, coordinator, key, name, unit, device_class, low, high) -> None:
        super().__init__(coordinator, key)
        self.key, self.low, self.high = key, low, high
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if key != "singleWorktime":
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if key in {"singleWorktime", "inGrlb", "outGrlb"}:
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        return number(self.coordinator.data, self.key, self.low, self.high)


class AlorairCoil(AlorairEntity, SensorEntity):
    _attr_name = "Coil temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "coil_temperature")

    @property
    def native_value(self) -> float | None:
        return number(self.coordinator.data, "coilTemperature", -100, 250)

    @property
    def native_unit_of_measurement(self):
        value = number(self.coordinator.data, "temperatureUnit", 0, 1)
        return UnitOfTemperature.FAHRENHEIT if value == 1 else UnitOfTemperature.CELSIUS if value == 0 else None


class AlorairFault(AlorairEntity, SensorEntity):
    _attr_name = "Fault codes"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "fault_codes")

    @property
    def native_value(self) -> str | None:
        value = faults(self.coordinator.data)
        return None if value is None else ", ".join(value) if value else "None"

    @property
    def extra_state_attributes(self):
        return {"raw_mask": code(self.coordinator.data, "errCode")}


class AlorairSampleTime(AlorairEntity, SensorEntity):
    _attr_name = "Device sample time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sample_time")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        value = vendor_datetime(self.coordinator.data)
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class AlorairCommandFeedback(AlorairEntity, SensorEntity):
    """Retain the last command result after a transient device flag clears."""

    _attr_name = "Last command"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:message-check-outline"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "last_command")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        feedback = self.coordinator.last_command
        if feedback is None:
            return "none"
        if feedback.status == "awaiting_feedback" and time.monotonic() >= feedback.deadline:
            return "not_observed"
        return feedback.status

    @property
    def extra_state_attributes(self):
        feedback = self.coordinator.last_command
        if feedback is None:
            return {}
        return {
            "action": feedback.action,
            "requested_at": feedback.issued_at,
            "requested_value": feedback.requested,
            "cloud_acknowledged": feedback.acknowledged,
            "device_reported_at": feedback.reported_at,
        }
