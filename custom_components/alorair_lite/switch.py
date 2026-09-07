"""Locator indicator control."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .entity import AlorairEntity
from .models import code


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([AlorairLocate(entry.runtime_data)])


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
