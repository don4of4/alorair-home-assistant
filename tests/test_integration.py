"""Exercise actual HA setup, services, configuration and recovery behavior."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
import pytest_asyncio
from homeassistant import config_entries, loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.alorair_lite.api import AuthenticationError, CannotConnect, CommandError, ProtocolError
from custom_components.alorair_lite.const import DOMAIN
from custom_components.alorair_lite.coordinator import AlorairCoordinator
from custom_components.alorair_lite.diagnostics import async_get_config_entry_diagnostics
from custom_components.alorair_lite.humidifier import AlorairHumidifier
from custom_components.alorair_lite.models import code, faults, normalize_mac, number, powered

MAC = "AABBCCDDEE01"


def entry() -> config_entries.ConfigEntry:
    return config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Test dehumidifier",
        data={"username": "test@example.invalid", "password": "TEST_NOT_REAL", "mac": MAC, "allow_insecure_http": True},
        options={},
        source="user",
        unique_id=MAC,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )


def status(**updates):
    return {
        "id": "unit1",
        "deviceNum": MAC,
        "powerStatus": "00",
        "currentHumidity": "50",
        "inHumidity": "57",
        "outHumidity": "30",
        "inCelsius": "20",
        "outCelsius": "25",
        "errCode": "00",
        "drainStatus": "00",
        "defrostingStatus": "00",
        "locateFunction": "00",
        "temperatureUnit": 0,
        "humidityUnit": 0,
        "updateTimeStr": datetime.now(UTC).isoformat(),
        **updates,
    }


@pytest_asyncio.fixture
async def hass(tmp_path):
    (tmp_path / "custom_components").symlink_to(
        Path(__file__).parents[1] / "custom_components", target_is_directory=True
    )
    instance = HomeAssistant(str(tmp_path))
    instance.config.skip_pip = True
    instance.config_entries = config_entries.ConfigEntries(instance, {})
    loader.async_setup(instance)
    assert await async_load_base_functionality(instance)
    assert await async_setup_component(instance, "homeassistant", {})
    await instance.async_start()
    async with aiohttp.ClientSession() as session:
        with (
            patch("custom_components.alorair_lite.async_get_clientsession", return_value=session),
            patch("custom_components.alorair_lite.config_flow.async_get_clientsession", return_value=session),
        ):
            yield instance
    for configured in instance.config_entries.async_entries(DOMAIN):
        await instance.config_entries.async_unload(configured.entry_id)
    await instance.async_stop()


def coordinator(hass, **updates):
    client = AsyncMock()
    client.async_status.return_value = status(**updates)
    result = AlorairCoordinator(hass, entry(), client, MAC)
    result.async_set_updated_data(client.async_status.return_value.copy())
    return result, client


def test_vendor_interpretation():
    assert normalize_mac("aa:bb:cc:dd:ee:01") == MAC
    assert normalize_mac("AA:BB") is None
    assert powered(status(powerStatus="02")) is True
    assert powered(status(powerStatus="00")) is False
    assert powered(status(powerStatus="bad")) is None
    assert number({"value": True}, "value") is None
    assert number({"value": "nan"}, "value") is None
    assert number({"value": 0}, "value") == 0
    assert faults(status(errCode="20")) == []
    assert faults(status(errCode="11")) == ["E1", "E4"]
    assert code({"errCode": "10"}, "errCode") == "10"


async def test_humidifier_uses_measurement_and_continuous_sentinel(hass):
    update, _ = coordinator(hass, currentHumidity="20", inHumidity="63", powerStatus="02")
    entity = AlorairHumidifier(update)
    assert entity.current_humidity == 63
    assert entity.target_humidity is None
    assert entity.mode == "continuous"
    assert entity.is_on is True
    assert entity.action is None  # No fabricated measured compressor state.


@pytest.mark.parametrize("timestamp", [None, "bad", "2020-01-01 00:00:00"])
async def test_missing_or_old_samples_disable_control(hass, timestamp):
    update, client = coordinator(hass, updateTimeStr=timestamp)
    assert update.status_stale
    assert not AlorairHumidifier(update).available
    with pytest.raises(HomeAssistantError):
        await update.async_command("async_set_power", (True,), "powerStatus", "01")
    client.async_set_power.assert_not_called()


async def test_future_samples_and_naive_utc(hass):
    update, _ = coordinator(hass, updateTimeStr=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
    assert update.status_stale
    update.data["updateTimeStr"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
    assert not update.status_stale


async def test_compressor_dwell_fault_and_requires_on(hass):
    update, client = coordinator(hass)
    with pytest.raises(ServiceValidationError):
        await update.async_command("async_set_power", (True,), "powerStatus", "01")
    update._last_off -= 181
    update.data["errCode"] = "10"
    with pytest.raises(ServiceValidationError):
        await update.async_command("async_set_power", (True,), "powerStatus", "01")
    update.data["errCode"] = "00"
    with pytest.raises(ServiceValidationError):
        await update.async_command("async_set_humidity", (55,), "currentHumidity", 55, requires_on=True)
    client.async_set_power.assert_not_called()


async def test_no_optimistic_state_and_pending_suppresses_duplicate(hass):
    update, client = coordinator(hass)
    update._last_off -= 181
    await update.async_command("async_set_power", (True,), "powerStatus", "01")
    assert powered(update.data) is False
    assert update.pending_power == "on"
    await update.async_command("async_set_power", (True,), "powerStatus", "01")
    client.async_set_power.assert_awaited_once_with(MAC, True)
    client.async_status.return_value = status(powerStatus="01")
    await update.async_refresh()
    assert powered(update.data) is True
    assert update.pending_power is None


async def test_ambiguous_delivery_retains_intent_but_explicit_rejection_does_not(hass):
    update, client = coordinator(hass, powerStatus="01")
    client.async_set_power.side_effect = CommandError("Uncertain", outcome_unknown=True)
    with pytest.raises(HomeAssistantError):
        await update.async_command("async_set_power", (False,), "powerStatus", "00")
    assert update.pending_power == "off"
    await update.async_command("async_set_power", (False,), "powerStatus", "00")
    client.async_set_power.assert_awaited_once()
    update.pending.clear()
    client.async_set_power.side_effect = CommandError("Rejected")
    with pytest.raises(HomeAssistantError):
        await update.async_command("async_set_power", (False,), "powerStatus", "00")
    assert update.pending_power is None


async def test_cancelled_on_finishes_before_boundary_off(hass):
    update, client = coordinator(hass)
    update._last_off -= 181
    started, release = asyncio.Event(), asyncio.Event()
    sequence = []

    async def send(mac, on):
        sequence.append(on)
        if on:
            started.set()
            await release.wait()

    client.async_set_power.side_effect = send
    on_task = asyncio.create_task(update.async_command("async_set_power", (True,), "powerStatus", "01"))
    await started.wait()
    on_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await on_task
    off_task = asyncio.create_task(update.async_command("async_set_power", (False,), "powerStatus", "00"))
    await asyncio.sleep(0)
    assert sequence == [True]
    release.set()
    await off_task
    assert sequence == [True, False]


async def test_status_failure_and_recovery(hass):
    update, client = coordinator(hass)
    client.async_status.side_effect = CannotConnect("offline")
    await update.async_refresh()
    assert not update.last_update_success
    assert not AlorairHumidifier(update).available
    client.async_status.side_effect = None
    await update.async_refresh()
    assert update.last_update_success
    assert AlorairHumidifier(update).available


async def test_diagnostics_exclude_identity_credentials_and_raw_strings(hass):
    update, _ = coordinator(hass, token="SECRET", name="SECRET", inHumidity="SECRET")
    configured = entry()
    configured.runtime_data = update
    result = await async_get_config_entry_diagnostics(hass, configured)
    assert "SECRET" not in str(result)
    assert MAC not in str(result)
    assert "test@example.invalid" not in str(result)


async def test_real_setup_entity_services_and_unload(hass):
    client = AsyncMock()
    data = status(powerStatus="01")
    client.async_status.side_effect = lambda mac: data.copy()

    async def set_humidity(mac, humidity):
        data["currentHumidity"] = str(humidity)

    client.async_set_humidity.side_effect = set_humidity
    configured = entry()
    with patch("custom_components.alorair_lite.AlorairClient", return_value=client):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
    assert configured.state is config_entries.ConfigEntryState.LOADED
    entities = hass.states.async_entity_ids("humidifier")
    assert len(entities) == 1
    entity_id = entities[0]
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(entity_id).attributes["current_humidity"] == 57
    await hass.services.async_call(
        "humidifier", "set_humidity", {"entity_id": entity_id, "humidity": 55}, blocking=True
    )
    assert hass.states.get(entity_id).attributes["humidity"] == 55
    client.async_set_humidity.assert_awaited_once_with(MAC, 55)
    await hass.services.async_call(
        "humidifier", "set_mode", {"entity_id": entity_id, "mode": "continuous"}, blocking=True
    )
    await configured.runtime_data.async_refresh()
    assert hass.states.get(entity_id).attributes["mode"] == "continuous"
    assert await hass.config_entries.async_unload(configured.entry_id)
    assert configured.state is config_entries.ConfigEntryState.NOT_LOADED


async def test_raw_24_hex_cloud_identity_keeps_mac_registry_and_command_selector(hass):
    """Cloud identity padding must not change HA identity or its public selector."""
    cloud_number = "000000000000" + MAC.lower()
    data = status(deviceNum=cloud_number, powerStatus="01")
    client = AsyncMock()
    client.async_status.side_effect = lambda mac: data.copy()

    async def set_power(mac, on):
        data.update(powerStatus="01" if on else "00", updateTimeStr=datetime.now(UTC).isoformat())

    client.async_set_power.side_effect = set_power
    configured = entry()
    with patch("custom_components.alorair_lite.AlorairClient", return_value=client):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
        assert configured.state is config_entries.ConfigEntryState.LOADED
        assert configured.data["mac"] == configured.unique_id == MAC
        assert configured.runtime_data.data["deviceNum"] == cloud_number

        entities = er.async_entries_for_config_entry(er.async_get(hass), configured.entry_id)
        registry_before = {(item.entity_id, item.unique_id) for item in entities}
        assert registry_before
        assert all(unique_id.startswith(MAC + "_") for _, unique_id in registry_before)
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), configured.entry_id)
        assert len(devices) == 1
        assert devices[0].identifiers == {(DOMAIN, MAC)}
        device_id = devices[0].id

        humidifier_id = next(item.entity_id for item in entities if item.domain == "humidifier")
        await hass.services.async_call("humidifier", "turn_off", {"entity_id": humidifier_id}, blocking=True)
        assert hass.states.get(humidifier_id).state == "off"
        client.async_set_power.assert_awaited_once_with(MAC, False)
        assert all(call.args == (MAC,) for call in client.async_status.await_args_list)

        assert await hass.config_entries.async_reload(configured.entry_id)
        await hass.async_block_till_done()
        assert configured.state is config_entries.ConfigEntryState.LOADED
        assert {
            (item.entity_id, item.unique_id)
            for item in er.async_entries_for_config_entry(er.async_get(hass), configured.entry_id)
        } == registry_before
        assert [item.id for item in dr.async_entries_for_config_entry(dr.async_get(hass), configured.entry_id)] == [
            device_id
        ]
        assert configured.runtime_data.data["deviceNum"] == cloud_number


async def test_config_flow_requires_disclosure_and_validates_identity(hass):
    with patch("custom_components.alorair_lite.config_flow.AlorairClient") as client_type:
        client_type.return_value = AsyncMock()
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == "menu"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
        assert result["type"] == "form"
        values = {
            "username": "test@example.invalid",
            "password": "TEST_NOT_REAL",
            "mac": MAC,
            "allow_insecure_http": False,
        }
        result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
        assert result["errors"] == {"allow_insecure_http": "http_required"}
        client_type.assert_not_called()
        values["allow_insecure_http"] = True
        client_type.return_value.async_login.side_effect = AuthenticationError("bad")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
        assert result["errors"] == {"base": "invalid_auth"}
        client_type.return_value.async_login.side_effect = None
        with patch.object(hass.config_entries, "async_setup", return_value=True):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
        assert result["type"] == "create_entry"
        assert result["result"].unique_id == MAC
        client_type.return_value.async_status.assert_awaited_once_with(MAC)


async def test_blank_username_returns_form_error_without_network(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "username": "   ",
            "password": "TEST_NOT_REAL",
            "mac": MAC,
            "allow_insecure_http": True,
        },
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize(
    ("phase", "stage", "message", "reason"),
    [
        ("login", "user_info", "The vendor returned an invalid account identity.", "account_identity_invalid"),
        (
            "status",
            "device_list",
            "The exact MAC was not found among this account's owned devices.",
            "owned_device_not_found",
        ),
        ("status", "device_detail", "Device detail did not match the requested identity.", "detail_identity_mismatch"),
        ("login", "login", "PRIVATE_PASSWORD PRIVATE_TOKEN PRIVATE_ID " + MAC, "unspecified_error"),
    ],
)
async def test_config_flow_failure_logs_only_fixed_diagnostics(hass, caplog, phase, stage, message, reason):
    error = ProtocolError(message, stage=stage)
    error.vendor_code = 403
    client = AsyncMock()
    getattr(client, "async_login" if phase == "login" else "async_status").side_effect = error
    with patch("custom_components.alorair_lite.config_flow.AlorairClient", return_value=client):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "username": "private@example.invalid",
                "password": "PRIVATE_PASSWORD",
                "mac": MAC,
                "allow_insecure_http": True,
            },
        )
    assert result["errors"] == {"base": "device_not_found"}
    assert f"phase={phase} stage={stage} reason={reason} vendor_code=403" in caplog.text
    for secret in ("PRIVATE_PASSWORD", "PRIVATE_TOKEN", "PRIVATE_ID", MAC, "private@example.invalid"):
        assert secret not in caplog.text


async def test_cached_off_after_uncertain_on_does_not_confirm_or_allow_restart(hass):
    update, client = coordinator(hass)
    update._last_off -= 181
    await update.async_command("async_set_power", (True,), "powerStatus", "01")
    await update.async_command("async_set_power", (False,), "powerStatus", "00")
    await update.async_refresh()  # Exactly the same pre-command sample.
    assert update.pending_power == "off"
    assert update.restart_delay_remaining == 180
    with pytest.raises(ServiceValidationError):
        await update.async_command("async_set_power", (True,), "powerStatus", "01")
    assert [call.args[1] for call in client.async_set_power.await_args_list] == [True, False]
    client.async_status.return_value = status(powerStatus="00")
    await update.async_refresh()
    assert update.pending_power is None
    assert update.restart_delay_remaining == 180


async def test_pending_off_blocks_on_even_when_device_still_reports_enabled(hass):
    update, client = coordinator(hass, powerStatus="01")
    await update.async_command("async_set_power", (False,), "powerStatus", "00")
    assert update.pending_power == "off"
    with pytest.raises(ServiceValidationError):
        await update.async_command("async_set_power", (True,), "powerStatus", "01")
    client.async_set_power.assert_awaited_once_with(MAC, False)


async def test_unload_drains_submitted_command_and_rejects_new_commands(hass):
    update, client = coordinator(hass)
    update._last_off -= 181
    started, release = asyncio.Event(), asyncio.Event()

    async def send(mac, on):
        started.set()
        await release.wait()

    client.async_set_power.side_effect = send
    command = asyncio.create_task(update.async_command("async_set_power", (True,), "powerStatus", "01"))
    await started.wait()
    unload = asyncio.create_task(update.async_prepare_unload())
    await asyncio.sleep(0)
    assert not unload.done()
    with pytest.raises(HomeAssistantError):
        await update.async_command("async_set_power", (False,), "powerStatus", "00")
    release.set()
    await command
    assert await unload
    client.async_set_power.assert_awaited_once_with(MAC, True)


async def test_ambiguous_command_triggers_feedback_read(hass):
    update, client = coordinator(hass, powerStatus="01")
    client.async_set_power.side_effect = CommandError("Uncertain", outcome_unknown=True)
    with pytest.raises(HomeAssistantError):
        await update.async_command("async_set_power", (False,), "powerStatus", "00")
    client.async_status.assert_awaited_once_with(MAC)


@pytest.mark.parametrize("cancel_unload", [False, True])
async def test_real_unload_drains_old_command_before_replacement(hass, cancel_unload):
    client = AsyncMock()
    client.async_status.side_effect = lambda mac: status()
    configured = entry()
    with patch("custom_components.alorair_lite.AlorairClient", return_value=client):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
    old = configured.runtime_data
    old._last_off -= 181
    started, release = asyncio.Event(), asyncio.Event()
    sequence = []

    async def old_send(mac, on):
        started.set()
        await release.wait()
        sequence.append(("old", on))

    client.async_set_power.side_effect = old_send
    command = asyncio.create_task(old.async_command("async_set_power", (True,), "powerStatus", "01"))
    await started.wait()
    closing = asyncio.Event()
    prepare_unload = old.async_prepare_unload

    async def prepare():
        closing.set()
        return await prepare_unload()

    old.async_prepare_unload = prepare
    unload = asyncio.create_task(hass.config_entries.async_unload(configured.entry_id))
    try:
        async with asyncio.timeout(2):
            await closing.wait()
        assert old._closing
        assert configured.state is config_entries.ConfigEntryState.UNLOAD_IN_PROGRESS
        assert not unload.done()
        if cancel_unload:
            unload.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not unload.done()
        with pytest.raises(HomeAssistantError, match="unloading"):
            await old.async_command("async_set_power", (False,), "powerStatus", "00")
        assert sequence == []
    finally:
        release.set()
        await command
        assert await unload
    assert configured.state is config_entries.ConfigEntryState.NOT_LOADED
    assert not old._command_tasks

    replacement = AsyncMock()
    replacement.async_status.side_effect = lambda mac: status(powerStatus="01")

    async def new_send(mac, on):
        sequence.append(("new", on))

    replacement.async_set_power.side_effect = new_send
    with patch("custom_components.alorair_lite.AlorairClient", return_value=replacement):
        assert await hass.config_entries.async_setup(configured.entry_id)
        await hass.async_block_till_done()
    assert configured.runtime_data is not old
    await configured.runtime_data.async_command("async_set_power", (False,), "powerStatus", "00")
    assert sequence == [("old", True), ("new", False)]
    client.async_set_power.assert_awaited_once_with(MAC, True)


async def test_real_unload_joins_command_deadline_without_failed_unload(hass):
    client = AsyncMock()
    client.async_status.side_effect = lambda mac: status()
    configured = entry()
    with patch("custom_components.alorair_lite.AlorairClient", return_value=client):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
    old = configured.runtime_data
    old._last_off -= 181
    started, release, terminated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    delivered = []

    async def slow_transaction(mac, on):
        started.set()
        try:
            await release.wait()
            delivered.append(on)
        finally:
            terminated.set()

    client.async_set_power.side_effect = slow_transaction
    with patch("custom_components.alorair_lite.coordinator.COMMAND_TIMEOUT_SECONDS", 0.05):
        command = asyncio.create_task(old.async_command("async_set_power", (True,), "powerStatus", "01"))
        await started.wait()
        assert await hass.config_entries.async_unload(configured.entry_id)
        with pytest.raises(HomeAssistantError, match="deadline expired.*not retried"):
            await command
    assert terminated.is_set()
    assert configured.state is config_entries.ConfigEntryState.NOT_LOADED
    assert not old._command_tasks
    assert old.pending_power == "on"
    client.async_set_power.assert_awaited_once_with(MAC, True)
    replacement = AsyncMock()
    replacement.async_status.side_effect = lambda mac: status()
    with patch("custom_components.alorair_lite.AlorairClient", return_value=replacement):
        assert await hass.config_entries.async_setup(configured.entry_id)
        await hass.async_block_till_done()
    release.set()
    await asyncio.sleep(0)
    assert configured.runtime_data is not old
    assert delivered == []
