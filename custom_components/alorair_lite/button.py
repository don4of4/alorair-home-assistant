"""Purge and status-refresh actions."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import AlorairEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([AlorairPurge(entry.runtime_data), AlorairRefresh(entry.runtime_data)])


class AlorairPurge(AlorairEntity, ButtonEntity):
    _attr_name = "Purge drain"
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "purge")

    async def async_press(self) -> None:
        self.require_supported_connection()
        await self.coordinator.async_command("async_purge", (), "drainStatus", "01", requires_on=True)


class AlorairRefresh(AlorairEntity, ButtonEntity):
    _attr_name = "Refresh status"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "refresh")

    @property
    def available(self) -> bool:
        return self.supported_by_connection

    async def async_press(self) -> None:
        self.require_supported_connection()
        await self.coordinator.async_request_refresh()
