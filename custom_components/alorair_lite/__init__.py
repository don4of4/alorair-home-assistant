"""ALORAIR-Lite integration setup."""

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AlorairClient
from .const import CONF_ALLOW_HTTP, CONF_MAC, DOMAIN
from .coordinator import AlorairCoordinator
from .services import async_register_services

PLATFORMS = [
    Platform.HUMIDIFIER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.SELECT,
]
type AlorairConfigEntry = ConfigEntry[AlorairCoordinator]


async def async_setup(hass: HomeAssistant, config) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AlorairConfigEntry) -> bool:
    client = AlorairClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        allow_insecure_http=entry.data.get(CONF_ALLOW_HTTP, False),
    )
    coordinator = AlorairCoordinator(hass, entry, client, entry.data[CONF_MAC])
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: AlorairConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AlorairConfigEntry) -> bool:
    async def finish_unload() -> bool:
        await entry.runtime_data.async_prepare_unload()
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    task = entry.async_create_task(hass, finish_unload(), f"{DOMAIN} unload")
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            # Finish the bounded cleanup even if the reload caller is cancelled.
            # Returning False would put HA into nonrecoverable FAILED_UNLOAD;
            # propagating cancellation now could allow replacement before drain.
            if caller := asyncio.current_task():
                caller.uncancel()
