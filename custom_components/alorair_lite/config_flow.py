"""Configure one explicitly selected ALORAIR-Lite device."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import AlorairClient, AlorairError, AuthenticationError, CannotConnect
from .const import (
    CONF_ALLOW_HTTP,
    CONF_DEVICE_HOST,
    CONF_EXPERIMENTAL,
    CONF_LISTEN_HOST,
    CONF_LISTEN_PORT,
    CONF_LOCAL_POWER,
    CONF_MAC,
    CONF_POLL_INTERVAL,
    CONF_TRANSPORT,
    DEFAULT_LOCAL_PORT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    TRANSPORT_CLOUD,
    TRANSPORT_LOCAL,
)
from .local_client import Config as LocalConfig
from .models import normalize_mac

_LOGGER = logging.getLogger(__name__)


def _log_setup_failure(phase: str, error: AlorairError) -> None:
    """Log fixed diagnostic labels and a bounded code, never exception text."""
    _LOGGER.warning(
        "ALORAIR setup failed: phase=%s stage=%s reason=%s vendor_code=%s",
        phase,
        error.diagnostic_stage,
        error.diagnostic_reason,
        error.vendor_code,
    )


class AlorairConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one device through cloud authentication or a local listener."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(step_id="user", menu_options=["cloud", "local"])

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None):
        return await self._credentials("cloud", user_input)

    async def async_step_local(self, user_input: dict[str, Any] | None = None):
        return await self._local("local", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(step_id="reconfigure", menu_options=["reconfigure_cloud", "reconfigure_local"])

    async def async_step_reconfigure_cloud(self, user_input: dict[str, Any] | None = None):
        return await self._credentials("reconfigure_cloud", user_input)

    async def async_step_reconfigure_local(self, user_input: dict[str, Any] | None = None):
        return await self._local("reconfigure_local", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        if entry_data.get(CONF_TRANSPORT) == TRANSPORT_LOCAL:
            return self.async_abort(reason="local_has_no_account")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        return await self._credentials("reauth_confirm", user_input)

    async def _credentials(self, step: str, user_input: dict[str, Any] | None):
        errors: dict[str, str] = {}
        entry = (
            self._get_reauth_entry()
            if step == "reauth_confirm"
            else self._get_reconfigure_entry()
            if step == "reconfigure_cloud"
            else None
        )
        if user_input is not None:
            mac = normalize_mac(entry.data[CONF_MAC] if entry else user_input.get(CONF_MAC))
            if not user_input.get(CONF_ALLOW_HTTP):
                errors[CONF_ALLOW_HTTP] = "http_required"
            elif not mac:
                errors[CONF_MAC] = "invalid_mac"
            else:
                data = {
                    CONF_TRANSPORT: TRANSPORT_CLOUD,
                    CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_ALLOW_HTTP: True,
                    CONF_MAC: mac,
                }
                phase = "login"
                try:
                    client = AlorairClient(
                        async_get_clientsession(self.hass),
                        data[CONF_USERNAME],
                        data[CONF_PASSWORD],
                        allow_insecure_http=True,
                    )
                    await client.async_login()
                    phase = "status"
                    await client.async_status(mac)
                except AuthenticationError as err:
                    _log_setup_failure(phase, err)
                    errors["base"] = "invalid_auth"
                except CannotConnect as err:
                    _log_setup_failure(phase, err)
                    errors["base"] = "cannot_connect"
                except AlorairError as err:
                    _log_setup_failure(phase, err)
                    errors["base"] = "device_not_found"
                else:
                    return await self._finish_entry(entry, data)
        schema: dict[Any, Any] = {
            vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "") if entry else ""): TextSelector(),
            vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }
        if not entry:
            schema[vol.Required(CONF_MAC)] = TextSelector()
        schema[vol.Required(CONF_ALLOW_HTTP, default=False)] = bool
        return self.async_show_form(step_id=step, data_schema=vol.Schema(schema), errors=errors)

    async def _local(self, step: str, user_input: dict[str, Any] | None):
        entry = self._get_reconfigure_entry() if step == "reconfigure_local" else None
        errors: dict[str, str] = {}
        if user_input is not None:
            mac = normalize_mac(entry.data[CONF_MAC] if entry else user_input.get(CONF_MAC))
            if not mac:
                errors[CONF_MAC] = "invalid_mac"
            else:
                try:
                    config = LocalConfig(
                        user_input[CONF_LISTEN_HOST],
                        user_input[CONF_LISTEN_PORT],
                        user_input[CONF_DEVICE_HOST],
                        mac,
                    )
                except ValueError:
                    errors["base"] = "invalid_local_address"
                else:
                    data = {
                        CONF_TRANSPORT: TRANSPORT_LOCAL,
                        CONF_MAC: mac,
                        CONF_LISTEN_HOST: config.listen_host,
                        CONF_LISTEN_PORT: config.listen_port,
                        CONF_DEVICE_HOST: config.allowed_client,
                    }
                    for configured in self._async_current_entries():
                        if configured is entry or configured.data.get(CONF_TRANSPORT) != TRANSPORT_LOCAL:
                            continue
                        if (
                            configured.data[CONF_LISTEN_HOST] == config.listen_host
                            and configured.data.get(CONF_LISTEN_PORT, DEFAULT_LOCAL_PORT) == config.listen_port
                            and configured.data[CONF_MAC] != mac
                        ):
                            errors["base"] = "listener_in_use"
                            break
                    if not errors:
                        return await self._finish_entry(entry, data)
        defaults = entry.data if entry else {}
        schema: dict[Any, Any] = {}
        if not entry:
            schema[vol.Required(CONF_MAC)] = TextSelector()
        for key in (CONF_LISTEN_HOST, CONF_DEVICE_HOST):
            schema[vol.Required(key, default=defaults.get(key, ""))] = TextSelector()
        schema[vol.Required(CONF_LISTEN_PORT, default=defaults.get(CONF_LISTEN_PORT, DEFAULT_LOCAL_PORT))] = vol.All(
            vol.Coerce(int), vol.Range(min=1024, max=65535)
        )
        return self.async_show_form(step_id=step, data_schema=vol.Schema(schema), errors=errors)

    async def _finish_entry(self, entry, data):
        mac = data[CONF_MAC]
        await self.async_set_unique_id(mac)
        if entry:
            self._abort_if_unique_id_mismatch()
            if data[CONF_TRANSPORT] == TRANSPORT_LOCAL:
                options = {
                    CONF_LOCAL_POWER: entry.data.get(CONF_TRANSPORT) == TRANSPORT_LOCAL
                    and entry.options.get(CONF_LOCAL_POWER, False)
                }
            else:
                options = {
                    CONF_POLL_INTERVAL: entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    CONF_EXPERIMENTAL: entry.options.get(CONF_EXPERIMENTAL, False),
                }
            # Replace data so a local entry retains no vendor credentials.
            return self.async_update_reload_and_abort(entry, data=data, options=options)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=f"ALORAIR {mac[-6:]}", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return AlorairOptionsFlow()


class AlorairOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if self.config_entry.data.get(CONF_TRANSPORT) == TRANSPORT_LOCAL:
            return await self.async_step_local(user_input)
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=self.config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=15, max=300)),
                    vol.Required(
                        CONF_EXPERIMENTAL, default=self.config_entry.options.get(CONF_EXPERIMENTAL, False)
                    ): bool,
                }
            ),
        )

    async def async_step_local(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data={CONF_LOCAL_POWER: user_input[CONF_LOCAL_POWER]})
        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema(
                {vol.Required(CONF_LOCAL_POWER, default=self.config_entry.options.get(CONF_LOCAL_POWER, False)): bool}
            ),
        )
