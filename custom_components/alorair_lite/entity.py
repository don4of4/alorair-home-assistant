"""Shared ALORAIR device identity."""

from typing import Any

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AlorairCoordinator
from .local_coordinator import LocalAlorairCoordinator

LOCAL_SUPPORTED_ENTITIES = frozenset(
    {
        "dehumidifier",
        "power",
        "temperatureUnit",
        "fresh",
        "sample_time",
        "last_command",
        "inHumidity",
        "outHumidity",
        "inCelsius",
        "outCelsius",
        "inGkg",
        "outGkg",
        "inGrlb",
        "outGrlb",
    }
)


class AlorairEntity(CoordinatorEntity[AlorairCoordinator | LocalAlorairCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: AlorairCoordinator | LocalAlorairCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.supported_by_connection = not coordinator.is_local or key in LOCAL_SUPPORTED_ENTITIES
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mac)},
            manufacturer="ALORAIR",
            name=coordinator.config_entry.title,
            model=coordinator.data.get("productName") or "AlorAir-Lite dehumidifier",
        )

    @property
    def available(self) -> bool:
        return self.supported_by_connection and super().available and not self.coordinator.status_stale

    @property
    def capability_attributes(self) -> dict[str, Any] | None:
        attributes = super().capability_attributes
        if not self.supported_by_connection:
            # HA excludes extra_state_attributes when an entity is unavailable.
            return {**(attributes or {}), "unavailable_reason": "not_supported_by_local_connection"}
        return attributes

    def require_supported_connection(self) -> None:
        if not self.supported_by_connection:
            raise ServiceValidationError("This feature is not supported by the local connection; use cloud mode")
