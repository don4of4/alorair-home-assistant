"""Preserve the HA entity contract when changing connection types."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from test_integration import MAC, entry, status
from test_integration import hass as hass
from test_local_integration import Appliance, available_port, eventually, local_data, local_entry

from custom_components.alorair_lite.const import DOMAIN

ENTITY_KEYS = {
    "binary_sensor": {"drainStatus", "defrostingStatus", "fault", "fresh"},
    "button": {"purge", "refresh"},
    "humidifier": {"dehumidifier"},
    "select": {"temperatureUnit", "humidityUnit"},
    "sensor": {
        "inHumidity",
        "outHumidity",
        "inCelsius",
        "outCelsius",
        "inGkg",
        "outGkg",
        "inGrlb",
        "outGrlb",
        "singleWorktime",
        "coil_temperature",
        "fault_codes",
        "sample_time",
        "last_command",
    },
    "switch": {"power", "locate"},
}
LOCAL_KEYS = {
    "dehumidifier",
    "power",
    "drainStatus",
    "temperatureUnit",
    "fresh",
    "sample_time",
    "last_command",
    "inHumidity",
    "outHumidity",
    "inCelsius",
    "outCelsius",
    "inGkg",
    "outGkg",
    "inGrlb",
    "outGrlb",
}
MEASUREMENT_UNITS = {
    "inHumidity": "%",
    "outHumidity": "%",
    "inCelsius": "°C",
    "outCelsius": "°C",
    "inGkg": "g/kg",
    "outGkg": "g/kg",
    "inGrlb": "gr/lb",
    "outGrlb": "gr/lb",
    "singleWorktime": "h",
}


def registry_entries(hass, configured):
    return er.async_entries_for_config_entry(er.async_get(hass), configured.entry_id)


def entity_snapshot(hass, configured):
    return {
        item.unique_id: (
            item.entity_id,
            item.original_name,
            item.name,
            item.icon,
            item.device_id,
            item.disabled_by,
        )
        for item in registry_entries(hass, configured)
    }


async def reconfigure(hass, configured, transport):
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": configured.entry_id}
    )
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": f"reconfigure_{transport}"})
    if transport == "local":
        values = local_data(available_port())
        values.pop("transport")
        values.pop("mac")
    else:
        values = {"username": "test@example.invalid", "password": "SYNTHETIC", "allow_insecure_http": True}
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], values)
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert configured.state is config_entries.ConfigEntryState.LOADED


@pytest.mark.parametrize("initial_transport", ["cloud", "local"])
async def test_entity_registry_and_customizations_survive_transport_round_trip(hass, initial_transport):
    cloud = AsyncMock()
    cloud.async_status.side_effect = lambda mac: status(
        powerStatus="01", errCode="01", inGkg="8", outGkg="6", inGrlb="56", outGrlb="42", singleWorktime="12"
    )
    configured = entry() if initial_transport == "cloud" else local_entry(available_port())
    registry = er.async_get(hass)
    with (
        patch("custom_components.alorair_lite.AlorairClient", return_value=cloud) as cloud_type,
        patch("custom_components.alorair_lite.config_flow.AlorairClient", return_value=cloud) as flow_cloud_type,
    ):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
        assert configured.state is config_entries.ConfigEntryState.LOADED
        original_entry_id = configured.entry_id
        assert {(item.domain, item.unique_id) for item in registry_entries(hass, configured)} == {
            (domain, f"{MAC}_{key}") for domain, keys in ENTITY_KEYS.items() for key in keys
        }

        # A user-chosen entity ID is also the reference used by dashboards and history.
        humidity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_inHumidity")
        registry.async_update_entity(
            humidity_id,
            new_entity_id="sensor.my_dehumidifier_humidity",
            name="My humidity reading",
            icon="mdi:water-percent",
        )
        for item in registry_entries(hass, configured):
            if item.disabled_by is not None:
                registry.async_update_entity(item.entity_id, disabled_by=None)
        working_id = registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_singleWorktime")
        registry.async_update_entity(working_id, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(configured.entry_id)
        await hass.async_block_till_done()
        expected = entity_snapshot(hass, configured)
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), configured.entry_id)
        assert len(devices) == 1
        assert devices[0].identifiers == {(DOMAIN, MAC)}
        device_id = devices[0].id

        alternate = "local" if initial_transport == "cloud" else "cloud"
        for index, transport in enumerate((initial_transport, alternate, initial_transport)):
            if index:
                await reconfigure(hass, configured, transport)
            assert configured.entry_id == original_entry_id and configured.unique_id == MAC
            assert configured.runtime_data.is_local == (transport == "local")
            assert entity_snapshot(hass, configured) == expected
            assert {item.device_id for item in registry_entries(hass, configured)} == {device_id}
            assert len(dr.async_entries_for_config_entry(dr.async_get(hass), configured.entry_id)) == 1
            assert hass.states.get(working_id) is None

            appliance = None
            if transport == "local":
                baseline_cloud_calls = (cloud_type.call_count, flow_cloud_type.call_count, cloud.mock_calls.copy())
                assert "username" not in configured.data and "password" not in configured.data
                reader, writer = await asyncio.open_connection(*configured.runtime_data.client.address)
                appliance = Appliance(reader, writer)
                appliance.fahrenheit = False
                await appliance.report()
                await eventually(lambda: not configured.runtime_data.status_stale)
            try:
                for item in registry_entries(hass, configured):
                    key = item.unique_id.removeprefix(f"{MAC}_")
                    if item.disabled_by is not None:
                        continue
                    state = hass.states.get(item.entity_id)
                    assert state is not None
                    if key in MEASUREMENT_UNITS:
                        assert state.attributes["unit_of_measurement"] == MEASUREMENT_UNITS[key]
                        assert state.attributes["state_class"] == "measurement"
                    if item.domain == "select":
                        assert state.attributes["options"] == (
                            ["celsius", "fahrenheit"]
                            if key == "temperatureUnit"
                            else ["grains_per_pound", "grams_per_kilogram"]
                        )
                    if transport == "local" and key not in LOCAL_KEYS:
                        assert state.state == "unavailable", key
                        assert state.attributes.get("unavailable_reason") == "not_supported_by_local_connection", key
                        entity = hass.data[DATA_INSTANCES][item.domain].get_entity(item.entity_id)
                        assert not entity.available
                        for value_property in ("native_value", "is_on", "current_option"):
                            if hasattr(entity, value_property):
                                assert getattr(entity, value_property) is None, key
                    else:
                        assert state.state != "unavailable", key
                        assert "unavailable_reason" not in state.attributes, key
                humidity = hass.states.get("sensor.my_dehumidifier_humidity")
                assert humidity.attributes["friendly_name"].endswith("My humidity reading")
                assert humidity.attributes["icon"] == "mdi:water-percent"
                fault_id = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{MAC}_fault")
                if transport == "cloud":
                    assert float(humidity.state) == 57
                    assert hass.states.get(fault_id).state == "on"
                else:
                    # Local measurements come from the appliance's own status report.
                    assert float(humidity.state) == 61
                    for key, measured in (("inGrlb", 63), ("outGrlb", 70)):
                        grains_id = registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_{key}")
                        assert float(hass.states.get(grains_id).state) == measured, key
                    assert hass.states.get(fault_id).state == "unavailable"
                    assert (cloud_type.call_count, flow_cloud_type.call_count, cloud.mock_calls) == baseline_cloud_calls
            finally:
                if appliance is not None:
                    await appliance.close()


async def test_shared_power_switch_uses_cloud_commands_and_reported_state(hass):
    cloud = AsyncMock()
    data = status(powerStatus="01")
    cloud.async_status.side_effect = lambda mac: data.copy()

    async def set_power(mac, enabled):
        data["powerStatus"] = "01" if enabled else "00"
        data["updateTimeStr"] = datetime.now(UTC).isoformat()

    cloud.async_set_power.side_effect = set_power
    configured = entry()
    with patch("custom_components.alorair_lite.AlorairClient", return_value=cloud):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
    power_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{MAC}_power")
    assert power_id is not None and hass.states.get(power_id).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": power_id}, blocking=True)
    assert hass.states.get(power_id).state == "off"
    configured.runtime_data._last_off -= 181
    await hass.services.async_call("switch", "turn_on", {"entity_id": power_id}, blocking=True)
    await configured.runtime_data.async_refresh()
    assert hass.states.get(power_id).state == "on"
    assert [call.args for call in cloud.async_set_power.await_args_list] == [(MAC, False), (MAC, True)]
