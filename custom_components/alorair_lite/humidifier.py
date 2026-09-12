"""Standard Home Assistant dehumidifier controls."""

from typing import Any

from homeassistant.components.humidifier import HumidifierDeviceClass, HumidifierEntity, HumidifierEntityFeature
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_LOCAL_POWER, MODE_AUTO, MODE_CONTINUOUS
from .entity import AlorairEntity
from .models import faults, number, powered


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    if entry.runtime_data.is_local:
        async_add_entities([AlorairLocalHumidifier(entry.runtime_data)])
        return
    async_add_entities([AlorairHumidifier(entry.runtime_data)])


class AlorairHumidifier(AlorairEntity, HumidifierEntity, RestoreEntity):
    _attr_name = None
    _attr_device_class = HumidifierDeviceClass.DEHUMIDIFIER
    _attr_supported_features = HumidifierEntityFeature.MODES
    _attr_available_modes = [MODE_AUTO, MODE_CONTINUOUS]
    _attr_min_humidity = 25
    _attr_max_humidity = 80
    _attr_target_humidity_step = 5

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "dehumidifier")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        previous = await self.async_get_last_state()
        waiting_for_local = self.coordinator.is_local and self.mode is None
        if (self.mode == MODE_CONTINUOUS or waiting_for_local) and previous:
            value = number(previous.attributes, "last_auto_humidity", 25, 80)
            if value is not None and value % 5 == 0:
                self.coordinator.last_auto_humidity = int(value)

    @property
    def is_on(self) -> bool | None:
        return powered(self.coordinator.data)

    @property
    def current_humidity(self) -> float | None:
        return number(self.coordinator.data, "inHumidity", 0, 100)

    @property
    def target_humidity(self) -> int | None:
        value = number(self.coordinator.data, "currentHumidity", 25, 80)
        return int(value) if value is not None else None

    @property
    def mode(self) -> str | None:
        value = number(self.coordinator.data, "currentHumidity", 20, 80)
        return None if value is None else MODE_CONTINUOUS if value == 20 else MODE_AUTO

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "pending_power": self.coordinator.pending_power,
            "restart_delay_remaining": self.coordinator.restart_delay_remaining,
            "last_auto_humidity": self.coordinator.last_auto_humidity,
            "fault_codes": faults(self.coordinator.data),
            "vendor_update_time": self.coordinator.data.get("updateTimeStr"),
            "cloud_polled_at": self.coordinator.data.get("observed_at_utc"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command("async_set_power", (True,), "powerStatus", "01")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command("async_set_power", (False,), "powerStatus", "00")

    async def async_set_humidity(self, humidity: int) -> None:
        if type(humidity) is not int or not 25 <= humidity <= 80 or humidity % 5:
            raise ServiceValidationError("Choose 25–80% in 5% steps; use continuous mode for CO")
        await self.coordinator.async_command(
            "async_set_humidity", (humidity,), "currentHumidity", humidity, requires_on=True
        )

    async def async_set_mode(self, mode: str) -> None:
        if mode not in self._attr_available_modes:
            raise ServiceValidationError("Choose auto or continuous")
        humidity = 20 if mode == MODE_CONTINUOUS else self.coordinator.last_auto_humidity
        await self.coordinator.async_command(
            "async_set_humidity", (humidity,), "currentHumidity", humidity, requires_on=True
        )


class AlorairLocalHumidifier(AlorairHumidifier):
    """Use the same humidifier identity with only verified native fields."""

    @property
    def available(self) -> bool:
        return self.coordinator.client.connected

    @property
    def is_on(self) -> bool | None:
        return super().is_on if not self.coordinator.status_stale else None

    @property
    def current_humidity(self) -> float | None:
        return super().current_humidity if not self.coordinator.status_stale else None

    @property
    def target_humidity(self) -> int | None:
        return super().target_humidity if not self.coordinator.status_stale else None

    @property
    def mode(self) -> str | None:
        return super().mode if not self.coordinator.status_stale else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "experimental_start_enabled": self.coordinator.config_entry.options.get(CONF_LOCAL_POWER, False),
            "pending_power": self.coordinator.pending_power,
            "restart_delay_remaining": self.coordinator.restart_delay_remaining,
            "last_auto_humidity": self.coordinator.last_auto_humidity,
            "local_received_at": self.coordinator.data.get("observed_at_utc"),
        }
