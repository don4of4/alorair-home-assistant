"""ALORAIR-Lite integration setup."""

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AlorairClient
from .const import (
    CONF_ALLOW_HTTP,
    CONF_DEVICE_HOST,
    CONF_LISTEN_HOST,
    CONF_LISTEN_PORT,
    CONF_MAC,
    CONF_TRANSPORT,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
    TRANSPORT_CLOUD,
    TRANSPORT_LOCAL,
)
from .coordinator import AlorairCoordinator
from .local_client import Config as LocalConfig
from .local_coordinator import LocalAlorairCoordinator
from .services import async_register_services

PLATFORMS = [
    Platform.HUMIDIFIER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.SELECT,
]
type AlorairConfigEntry = ConfigEntry[AlorairCoordinator | LocalAlorairCoordinator]


async def async_setup(hass: HomeAssistant, config) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AlorairConfigEntry) -> bool:
    transport = entry.data.get(CONF_TRANSPORT, TRANSPORT_CLOUD)
    if transport == TRANSPORT_LOCAL:
        try:
            config = LocalConfig(
                listen_host=entry.data[CONF_LISTEN_HOST],
                listen_port=entry.data.get(CONF_LISTEN_PORT, DEFAULT_LOCAL_PORT),
                allowed_client=entry.data[CONF_DEVICE_HOST],
                mac=entry.data[CONF_MAC],
                status_max_age=35,
            )
        except (ValueError, KeyError) as err:
            raise ConfigEntryError("Reconfigure the experimental local listener settings") from err
        coordinator = LocalAlorairCoordinator(hass, entry, config)
        try:
            await coordinator.async_start()
        except OSError as err:
            await coordinator.async_prepare_unload()
            raise ConfigEntryNotReady("The local listener could not bind; check its address and port") from err

        async def async_stop_local_listener(event) -> None:
            await coordinator.async_prepare_unload()

        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_local_listener))
    elif transport == TRANSPORT_CLOUD:
        client = AlorairClient(
            async_get_clientsession(hass),
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            allow_insecure_http=entry.data.get(CONF_ALLOW_HTTP, False),
        )
        coordinator = AlorairCoordinator(hass, entry, client, entry.data[CONF_MAC])
        await coordinator.async_config_entry_first_refresh()
    else:
        raise ConfigEntryError("Unsupported ALORAIR connection type; reconfigure this entry")
    entry.runtime_data = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_prepare_unload()
        raise
    return True


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
