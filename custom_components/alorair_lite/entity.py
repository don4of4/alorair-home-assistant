"""Shared ALORAIR device identity."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AlorairCoordinator


class AlorairEntity(CoordinatorEntity[AlorairCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: AlorairCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mac)},
            manufacturer="ALORAIR",
            name=coordinator.config_entry.title,
            model=coordinator.data.get("productName") or "AlorAir-Lite dehumidifier",
        )

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.status_stale
