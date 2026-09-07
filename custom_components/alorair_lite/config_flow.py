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
from .const import CONF_ALLOW_HTTP, CONF_EXPERIMENTAL, CONF_MAC, CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL, DOMAIN
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
    """Authenticate only after the vendor HTTP disclosure is accepted."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._credentials("user", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        return await self._credentials("reauth_confirm", user_input)

    async def _credentials(self, step: str, user_input: dict[str, Any] | None):
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry() if step == "reauth_confirm" else None
        if user_input is not None:
            mac = normalize_mac(entry.data[CONF_MAC] if entry else user_input.get(CONF_MAC))
            if not user_input.get(CONF_ALLOW_HTTP):
                errors[CONF_ALLOW_HTTP] = "http_required"
            elif not mac:
                errors[CONF_MAC] = "invalid_mac"
            else:
                data = {
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
                    await self.async_set_unique_id(mac)
                    if entry:
                        self._abort_if_unique_id_mismatch()
                        return self.async_update_reload_and_abort(entry, data_updates=data)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=f"ALORAIR {mac[-6:]}", data=data)
        schema: dict[Any, Any] = {
            vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME] if entry else ""): TextSelector(),
            vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }
        if not entry:
            schema[vol.Required(CONF_MAC)] = TextSelector()
        schema[vol.Required(CONF_ALLOW_HTTP, default=False)] = bool
        return self.async_show_form(step_id=step, data_schema=vol.Schema(schema), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return AlorairOptionsFlow()


class AlorairOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
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
