"""Display units reported by the device."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError

from .entity import AlorairEntity
from .models import number


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    entities = [
        AlorairUnit(
            entry.runtime_data,
            "temperatureUnit",
            "Temperature display",
            ["celsius", "fahrenheit"],
            "async_set_temperature_unit",
        )
    ]
    if not entry.runtime_data.is_local:
        entities.append(
            AlorairUnit(
                entry.runtime_data,
                "humidityUnit",
                "Specific humidity display",
                ["grains_per_pound", "grams_per_kilogram"],
                "async_set_moisture_unit",
            )
        )
    async_add_entities(entities)


class AlorairUnit(AlorairEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, key: str, name: str, options: list[str], method: str) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_options = options
        self.key = key
        self.method = method

    @property
    def current_option(self) -> str | None:
        value = number(self.coordinator.data, self.key, 0, 1)
        return self._attr_options[int(value)] if value in {0, 1} else None

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            raise ServiceValidationError("Unsupported display unit")
        await self.coordinator.async_command(
            self.method, (option,), self.key, f"{self._attr_options.index(option):02d}"
        )
