"""Exercise the local profile through actual HA entries, services and sockets."""

import asyncio
import socket
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from homeassistant import config_entries
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from test_integration import MAC, entry, status
from test_integration import hass as hass

from custom_components.alorair_lite.const import DOMAIN
from custom_components.alorair_lite.diagnostics import async_get_config_entry_diagnostics
from custom_components.alorair_lite.local_protocol import Frame, FrameStream


def local_data(port=0, mac=MAC):
    return {
        "transport": "local",
        "mac": mac,
        "listen_host": "127.0.0.1",
        "device_host": "127.0.0.1",
        "listen_port": port,
    }


def local_entry(port=0):
    return config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Test local dehumidifier",
        data=local_data(port),
        options={},
        source="user",
        unique_id=MAC,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110 - bounded observation of HA and socket state
            await asyncio.sleep(0.005)


class Appliance:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.parser = FrameStream(bytes.fromhex(MAC))
        self.power = False
        self.fahrenheit = True
        self.target = 20
        # Realistic measured values as (celsius, fahrenheit, humidity); tests override these.
        self.inlet = (20, 68, 61)
        self.outlet = (18, 64, 75)
        self.inlet_grlb, self.inlet_gkg = 63, 9
        self.outlet_grlb, self.outlet_gkg = 70, 10

    async def report(self, opcode=0x1C):
        data = bytearray(34)
        data[3] = int(self.power)
        data[32] = int(self.fahrenheit)
        data[23] = self.target
        data[8:11] = bytes(self.inlet)
        data[11:14] = bytes(self.outlet)
        data[14:16] = self.inlet_grlb.to_bytes(2, "big")
        data[16:18] = self.inlet_gkg.to_bytes(2, "big")
        data[18:20] = self.outlet_grlb.to_bytes(2, "big")
        data[20:22] = self.outlet_gkg.to_bytes(2, "big")
        self.writer.write(Frame(bytes.fromhex(MAC), 0, 7, opcode, bytes(data)).encode())
        await self.writer.drain()

    async def command(self):
        async with asyncio.timeout(3):
            while True:
                chunk = await self.reader.read(2048)
                if not chunk:
                    raise AssertionError("The local connection closed before a command")
                frames = self.parser.feed(chunk)
                for _, frame in frames:
                    if frame.opcode != 1:
                        assert frame.function == 9
                        return frame

    async def close(self):
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except ConnectionResetError, BrokenPipeError:
            pass


@pytest_asyncio.fixture
async def unit(hass, monkeypatch):
    monkeypatch.setattr("custom_components.alorair_lite.local_client.COMMAND_SPACING", 0.001)
    configured = local_entry()
    with patch("custom_components.alorair_lite.AlorairClient", side_effect=AssertionError("Unexpected cloud client")):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
    assert configured.state is config_entries.ConfigEntryState.LOADED
    assert configured.runtime_data.data == {}
    reader, writer = await asyncio.open_connection(*configured.runtime_data.client.address)
    appliance = Appliance(reader, writer)
    try:
        yield configured, appliance
    finally:
        await appliance.close()


async def test_local_entry_waits_for_device_without_cloud_or_fabricated_readings(hass, unit):
    configured, appliance = unit
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, configured.entry_id)
    assert {item.unique_id for item in entities} == {
        f"{MAC}_{key}"
        for key in (
            "power",
            "dehumidifier",
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
            "singleWorktime",
            "coil_temperature",
            "fault_codes",
            "drainStatus",
            "defrostingStatus",
            "fault",
            "locate",
            "purge",
            "refresh",
            "humidityUnit",
        )
    }
    power_id = registry.async_get_entity_id("switch", DOMAIN, f"{MAC}_power")
    assert hass.states.get(power_id).state == "unavailable"
    await appliance.report()
    await eventually(lambda: hass.states.get(power_id).state == "off")
    diagnostics = await async_get_config_entry_diagnostics(hass, configured)
    assert diagnostics["transport"] == "local_tcp_push"
    assert diagnostics["state"]["errCode"] is None
    assert diagnostics["measurements"]["currentHumidity"] == 20
    assert all(value not in str(diagnostics) for value in (MAC, "127.0.0.1", "password", "token"))
    await appliance.close()
    await eventually(lambda: hass.states.get(power_id).state == "unavailable")


async def test_ha_display_service_waits_for_native_reply(hass, unit):
    configured, appliance = unit
    await appliance.report()
    await eventually(lambda: not configured.runtime_data.status_stale)
    registry = er.async_get(hass)
    select_id = registry.async_get_entity_id("select", DOMAIN, f"{MAC}_temperatureUnit")
    action = asyncio.create_task(
        hass.services.async_call(
            "select", "select_option", {"entity_id": select_id, "option": "celsius"}, blocking=True
        )
    )
    frame = await appliance.command()
    assert (frame.opcode, frame.data) == (0x24, b"\x00")
    assert not action.done()
    assert hass.states.get(select_id).state == "fahrenheit"
    appliance.fahrenheit = False
    await appliance.report(0x24)
    await action
    await eventually(lambda: hass.states.get(select_id).state == "celsius")
    feedback_id = registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_last_command")
    assert hass.states.get(feedback_id).state == "device_reported"
    assert "cloud_acknowledged" not in hass.states.get(feedback_id).attributes


async def test_ha_power_option_dwell_and_confirmed_stop(hass, unit):
    configured, appliance = unit
    await appliance.report()
    await eventually(lambda: not configured.runtime_data.status_stale)
    registry = er.async_get(hass)
    power_id = registry.async_get_entity_id("switch", DOMAIN, f"{MAC}_power")
    with pytest.raises(ServiceValidationError, match="experimental local power"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": power_id}, blocking=True)
    hass.config_entries.async_update_entry(configured, options={"allow_local_power": True})
    with pytest.raises(ServiceValidationError, match="restart protection"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": power_id}, blocking=True)
    configured.runtime_data._last_off -= 181
    for enabled in (True, False):
        action = asyncio.create_task(
            hass.services.async_call(
                "switch", "turn_on" if enabled else "turn_off", {"entity_id": power_id}, blocking=True
            )
        )
        command = await appliance.command()
        assert (command.opcode, command.data) == (0x21, bytes([int(enabled)]))
        assert not action.done()
        appliance.power = enabled
        await appliance.report(0x21)
        await action
        await eventually(lambda enabled=enabled: hass.states.get(power_id).state == ("on" if enabled else "off"))


async def test_real_local_reload_releases_and_rebinds_listener(hass):
    configured = local_entry(available_port())
    await hass.config_entries.async_add(configured)
    await hass.async_block_till_done()
    old = configured.runtime_data
    address = old.client.address
    assert await hass.config_entries.async_reload(configured.entry_id)
    assert configured.runtime_data is not old
    assert configured.runtime_data.client.address == address
    assert old._watchdog_task.done()
    assert not old.client.connected
    assert await hass.config_entries.async_unload(configured.entry_id)
    listener = await asyncio.start_server(lambda reader, writer: writer.close(), *address)
    listener.close()
    await listener.wait_closed()


@pytest.mark.parametrize("cancel_reload", [False, True])
async def test_local_reload_waits_for_inflight_native_command(hass, unit, cancel_reload):
    configured, appliance = unit
    old = configured.runtime_data
    await appliance.report()
    await eventually(lambda: not old.status_stale)
    command = asyncio.create_task(
        old.async_command("async_set_temperature_unit", ("celsius",), "temperatureUnit", "00")
    )
    assert (await appliance.command()).opcode == 0x24
    reload = asyncio.create_task(hass.config_entries.async_reload(configured.entry_id))
    await eventually(lambda: old._closing)
    if cancel_reload:
        reload.cancel()
        await asyncio.sleep(0)
    assert not reload.done()
    assert configured.runtime_data is old
    with pytest.raises(HomeAssistantError, match="unloading"):
        await old.async_command("async_set_power", (False,), "powerStatus", "00")
    appliance.fahrenheit = False
    await appliance.report(0x24)
    await command
    assert await reload
    assert configured.runtime_data is not old
    assert configured.state is config_entries.ConfigEntryState.LOADED
    assert old._watchdog_task.done() and not old.client.connected


async def test_local_setup_bind_failure_can_retry_after_conflict_removed(hass):
    listener = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
    configured = local_entry(listener.sockets[0].getsockname()[1])
    try:
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
        assert configured.state is config_entries.ConfigEntryState.SETUP_RETRY
    finally:
        listener.close()
        await listener.wait_closed()
    assert await hass.config_entries.async_reload(configured.entry_id)
    assert configured.state is config_entries.ConfigEntryState.LOADED


async def test_home_assistant_stop_closes_local_socket_without_entry_unload(hass, unit):
    configured, appliance = unit
    await appliance.report()
    await eventually(lambda: not configured.runtime_data.status_stale)
    coordinator = configured.runtime_data
    address = coordinator.client.address
    await hass.async_stop()
    assert coordinator._closing
    assert coordinator._watchdog_task.done()
    assert not coordinator.client.connected
    assert await appliance.reader.read(1) == b""
    listener = await asyncio.start_server(lambda reader, writer: writer.close(), *address)
    listener.close()
    await listener.wait_closed()


async def local_form(hass):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert flow["type"] == "menu"
    return await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "local"})


async def test_local_config_flow_normalizes_identity_without_credentials_or_cloud(hass):
    with patch("custom_components.alorair_lite.config_flow.AlorairClient", side_effect=AssertionError("Cloud call")):
        flow = await local_form(hass)
        values = local_data(available_port(), "aa:bb:cc:dd:ee:01")
        values.pop("transport")
        invalid = {**values, "device_host": "0.0.0.0"}
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], invalid)
        assert flow["errors"] == {"base": "invalid_local_address"}
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], values)
        assert result["type"] == "create_entry"
        assert result["result"].unique_id == MAC
        assert result["result"].data == local_data(values["listen_port"])
        await hass.async_block_till_done()
        duplicate = await local_form(hass)
        result = await hass.config_entries.flow.async_configure(duplicate["flow_id"], values)
        assert result["type"] == "abort"
        assert result["reason"] == "already_configured"


async def test_reconfigure_replaces_cloud_credentials_and_retains_entry_identity(hass):
    cloud = AsyncMock()
    cloud.async_status.side_effect = lambda mac: status()
    configured = entry()
    with patch("custom_components.alorair_lite.AlorairClient", return_value=cloud) as cloud_type:
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
        original_id = configured.entry_id
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": original_id}
        )
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "reconfigure_local"})
        values = local_data(available_port())
        values.pop("transport")
        values.pop("mac")
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], values)
        assert result["type"] == "abort"
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert configured.state is config_entries.ConfigEntryState.LOADED
        assert configured.runtime_data.is_local
        assert configured.entry_id == original_id and configured.unique_id == MAC
        assert "username" not in configured.data and "password" not in configured.data
        assert configured.options == {"allow_local_power": False}
        cloud_type.assert_called_once()


async def test_reconfigure_back_to_cloud_closes_listener_and_reauthenticates(hass, unit):
    configured, appliance = unit
    old = configured.runtime_data
    address = old.client.address
    cloud = AsyncMock()
    cloud.async_status.side_effect = lambda mac: status()
    with (
        patch("custom_components.alorair_lite.AlorairClient", return_value=cloud),
        patch("custom_components.alorair_lite.config_flow.AlorairClient", return_value=cloud),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": configured.entry_id}
        )
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "reconfigure_cloud"})
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {"username": "test@example.invalid", "password": "SYNTHETIC", "allow_insecure_http": True},
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert configured.state is config_entries.ConfigEntryState.LOADED
        assert not configured.runtime_data.is_local
        assert configured.unique_id == MAC
        assert set(configured.data) == {"transport", "mac", "username", "password", "allow_insecure_http"}
        assert "allow_local_power" not in configured.options
        cloud.async_login.assert_awaited_once()
    assert not old.client.connected and old._watchdog_task.done()
    assert await appliance.reader.read(1) == b""
    listener = await asyncio.start_server(lambda reader, writer: writer.close(), *address)
    listener.close()
    await listener.wait_closed()


async def test_duplicate_listener_is_rejected_before_another_device_entry(hass, unit):
    configured, _ = unit
    # The runtime's ephemeral test port is stored here to exercise normal config.
    data = dict(configured.data)
    data["listen_port"] = configured.runtime_data.client.address[1]
    hass.config_entries.async_update_entry(configured, data=data)
    flow = await local_form(hass)
    values = {**data, "mac": "AABBCCDDEE02"}
    values.pop("transport")
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], values)
    assert result["errors"] == {"base": "listener_in_use"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_options_reload_local_listener_once_and_do_not_add_cloud_options(hass, unit):
    configured, _ = unit
    old = configured.runtime_data
    flow = await hass.config_entries.options.async_init(configured.entry_id)
    assert flow["step_id"] == "local"
    with patch.object(hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload) as reload:
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"allow_local_power": True})
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        assert configured.runtime_data is not old
        reload.assert_awaited_once_with(configured.entry_id)
    assert configured.options == {"allow_local_power": True}


async def test_unsupported_local_target_and_cloud_action_do_not_write(hass, unit):
    configured, appliance = unit
    await appliance.report()
    await eventually(lambda: not configured.runtime_data.status_stale)
    with pytest.raises(HomeAssistantError, match="not supported"):
        await configured.runtime_data.async_command("async_set_humidity", (51,), "currentHumidity", 51)
    with pytest.raises(HomeAssistantError, match="cloud actions"):
        await configured.runtime_data.async_experimental_action("firmware_check", {})
    registry = er.async_get(hass)
    with (
        patch.object(configured.runtime_data, "async_command", new_callable=AsyncMock) as command,
        patch.object(configured.runtime_data, "async_request_refresh", new_callable=AsyncMock) as refresh,
    ):
        for domain, key, method, args in (
            ("button", "purge", "async_press", ()),
            ("button", "refresh", "async_press", ()),
            ("switch", "locate", "async_turn_on", ()),
            ("switch", "locate", "async_turn_off", ()),
            ("select", "humidityUnit", "async_select_option", ("grams_per_kilogram",)),
        ):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{MAC}_{key}")
            entity = hass.data[DATA_INSTANCES][domain].get_entity(entity_id)
            assert hass.states.get(entity_id).state == "unavailable"
            with pytest.raises(ServiceValidationError, match="local"):
                await getattr(entity, method)(*args)
        command.assert_not_awaited()
        refresh.assert_not_awaited()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(appliance.reader.read(1), 0.02)


async def test_local_humidifier_services_confirm_targets_and_preserve_identity(hass, unit):
    configured, appliance = unit
    appliance.power = True
    await appliance.report()
    await eventually(lambda: not configured.runtime_data.status_stale)
    registry = er.async_get(hass)
    humidifier_id = registry.async_get_entity_id("humidifier", DOMAIN, f"{MAC}_dehumidifier")
    state = hass.states.get(humidifier_id)
    assert state.state == "on" and state.attributes["mode"] == "continuous"
    assert state.attributes["current_humidity"] == 61
    assert "fault_codes" not in state.attributes
    assert "cloud_polled_at" not in state.attributes
    for service, parameters, target in (
        ("set_humidity", {"humidity": 50}, 50),
        ("set_humidity", {"humidity": 55}, 55),
        ("set_mode", {"mode": "continuous"}, 20),
        ("set_mode", {"mode": "auto"}, 55),
    ):
        previous = configured.runtime_data.data["currentHumidity"]
        action = asyncio.create_task(
            hass.services.async_call("humidifier", service, {"entity_id": humidifier_id, **parameters}, blocking=True)
        )
        command = await appliance.command()
        assert (command.opcode, command.data) == (0x23, bytes([target]))
        assert not action.done()
        assert configured.runtime_data.data["currentHumidity"] == previous
        appliance.target = target
        await appliance.report(0x23)
        await action
        assert configured.runtime_data.data["currentHumidity"] == target
    await eventually(lambda: hass.states.get(humidifier_id).attributes["humidity"] == 55)
    assert hass.states.get(humidifier_id).attributes["last_auto_humidity"] == 55


async def test_local_measurements_are_reported_by_the_device_and_clear_when_stale(hass, unit):
    configured, appliance = unit
    await appliance.report()
    await eventually(lambda: not configured.runtime_data.status_stale)
    registry = er.async_get(hass)
    expected = {
        "inHumidity": (61, "%"),
        "outHumidity": (75, "%"),
        "inCelsius": (20, "°C"),
        "outCelsius": (18, "°C"),
        "inGkg": (9, "g/kg"),
        "outGkg": (10, "g/kg"),
    }
    measurements = {key: registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_{key}") for key in expected}
    for key, (value, units) in expected.items():
        state = hass.states.get(measurements[key])
        assert float(state.state) == value, key
        assert state.attributes["unit_of_measurement"] == units, key
    humidifier_id = registry.async_get_entity_id("humidifier", DOMAIN, f"{MAC}_dehumidifier")
    assert hass.states.get(humidifier_id).attributes["current_humidity"] == 61
    configured.runtime_data._received_monotonic -= 36
    configured.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    for key, entity_id in measurements.items():
        assert hass.states.get(entity_id).state == "unavailable", key
        assert "unavailable_reason" not in hass.states.get(entity_id).attributes, key
    assert hass.states.get(humidifier_id).attributes.get("current_humidity") is None


async def test_local_restores_auto_target_before_initial_device_connection_without_commands(hass):
    configured = local_entry()
    previous = State("humidifier.previous", "off", {"last_auto_humidity": 55})
    with patch(
        "custom_components.alorair_lite.humidifier.AlorairLocalHumidifier.async_get_last_state",
        AsyncMock(return_value=previous),
    ):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
    assert configured.runtime_data.last_auto_humidity == 55
    assert configured.runtime_data.data == {}
    assert configured.runtime_data.last_command is None


async def test_local_humidifier_hides_stale_fields_but_keeps_normal_off_reachable(hass, unit):
    configured, appliance = unit
    appliance.power = True
    appliance.target = 50
    await appliance.report()
    await eventually(lambda: not configured.runtime_data.status_stale)
    humidifier_id = er.async_get(hass).async_get_entity_id("humidifier", DOMAIN, f"{MAC}_dehumidifier")
    configured.runtime_data._received_monotonic -= 36
    configured.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get(humidifier_id)
    assert state.state == "unknown"
    assert state.attributes["mode"] is None and state.attributes.get("humidity") is None
    assert state.attributes.get("current_humidity") is None
    with pytest.raises(ServiceValidationError, match="Fresh local"):
        await hass.services.async_call(
            "humidifier", "set_humidity", {"entity_id": humidifier_id, "humidity": 55}, blocking=True
        )
    action = asyncio.create_task(
        hass.services.async_call("humidifier", "turn_off", {"entity_id": humidifier_id}, blocking=True)
    )
    command = await appliance.command()
    assert (command.opcode, command.data) == (0x21, b"\x00")
    appliance.power = False
    await appliance.report(0x21)
    await action
    await eventually(lambda: hass.states.get(humidifier_id).state == "off")
