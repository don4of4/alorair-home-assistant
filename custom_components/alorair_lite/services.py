"""Opt-in, device-scoped experimental cloud actions."""

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .api import EXPERIMENTAL_ACTIONS
from .const import DOMAIN

SERVICE_EXPERIMENTAL = "experimental_action"
SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("action"): vol.In(EXPERIMENTAL_ACTIONS),
        vol.Optional("period"): vol.In(("day", "month", "year")),
        vol.Optional("query_date"): cv.string,
        vol.Optional("label"): cv.string,
        vol.Optional("filter_id"): cv.string,
        vol.Optional("extension"): vol.All(vol.Coerce(int), vol.In((1, 2, 3))),
        vol.Optional("page"): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    async def handle(call: ServiceCall) -> dict[str, Any]:
        device = dr.async_get(hass).async_get(call.data["device_id"])
        if device is None:
            raise ServiceValidationError("Select an ALORAIR Lite device registered in Home Assistant")
        entries = [
            entry
            for entry_id in device.config_entries
            if (entry := hass.config_entries.async_get_entry(entry_id)) and entry.domain == DOMAIN
        ]
        if len(entries) != 1 or entries[0].state is not ConfigEntryState.LOADED:
            raise ServiceValidationError("The selected ALORAIR Lite device must have one loaded integration entry")
        if (DOMAIN, entries[0].runtime_data.mac) not in device.identifiers:
            raise ServiceValidationError("The selected device identity does not match this ALORAIR Lite entry")
        parameters = {k: v for k, v in call.data.items() if k not in {"device_id", "action"}}
        if call.data["action"] in {"history", "operation_time"}:
            parameters.setdefault("period", "day")
            parameters.setdefault("query_date", dt_util.now().date().isoformat())
        return await entries[0].runtime_data.async_experimental_action(call.data["action"], parameters)

    hass.services.async_register(
        DOMAIN, SERVICE_EXPERIMENTAL, handle, SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
