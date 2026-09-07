"""Reported purge, defrost, fault and sample freshness."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import AlorairEntity
from .models import code, faults


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(
        [
            AlorairBinary(entry.runtime_data, key, name)
            for key, name in (
                ("drainStatus", "Draining"),
                ("defrostingStatus", "Defrosting"),
                ("fault", "Problem"),
                ("fresh", "Fresh device sample"),
            )
        ]
    )


class AlorairBinary(AlorairEntity, BinarySensorEntity):
    def __init__(self, coordinator, key: str, name: str) -> None:
        super().__init__(coordinator, key)
        self.key = key
        self._attr_name = name
        if key in {"fault", "fresh"}:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_device_class = (
                BinarySensorDeviceClass.PROBLEM if key == "fault" else BinarySensorDeviceClass.CONNECTIVITY
            )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success if self.key == "fresh" else super().available

    @property
    def is_on(self) -> bool | None:
        if self.key == "fresh":
            return not self.coordinator.status_stale
        if self.key == "fault":
            value = faults(self.coordinator.data)
            return bool(value) if value is not None else None
        value = code(self.coordinator.data, self.key)
        return value == "01" if value in {"00", "01"} else None
