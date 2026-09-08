"""Locator indicator control."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .const import CONF_LOCAL_POWER
from .entity import AlorairEntity
from .models import code, powered


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    if entry.runtime_data.is_local:
        async_add_entities([AlorairLocalPower(entry.runtime_data)])
        return
    async_add_entities([AlorairLocate(entry.runtime_data)])


class AlorairLocalPower(AlorairEntity, SwitchEntity):
    """Experimental enabled-state control; no measured compressor state."""

    _attr_name = "Power"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def available(self) -> bool:
        # Keep normal OFF reachable on a verified session even if telemetry stops.
        return self.coordinator.client.connected

    @property
    def is_on(self) -> bool | None:
        return powered(self.coordinator.data) if not self.coordinator.status_stale else None

    @property
    def extra_state_attributes(self):
        return {
            "experimental_start_enabled": self.coordinator.config_entry.options.get(CONF_LOCAL_POWER, False),
            "pending_power": self.coordinator.pending_power,
            "restart_delay_remaining": self.coordinator.restart_delay_remaining,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command("async_set_power", (True,), "powerStatus", "01")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command("async_set_power", (False,), "powerStatus", "00")


class AlorairLocate(AlorairEntity, SwitchEntity):
    _attr_name = "Locator"
    _attr_icon = "mdi:map-marker-radius"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "locate")

    @property
    def is_on(self) -> bool | None:
        value = code(self.coordinator.data, "locateFunction")
        return value == "01" if value in {"00", "01"} else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command("async_set_locate", (True,), "locateFunction", "01", requires_on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command("async_set_locate", (False,), "locateFunction", "00")
