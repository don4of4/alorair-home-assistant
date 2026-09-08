"""Exercise local push state, guarded controls and lifecycle in actual HA."""

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from test_integration import MAC
from test_integration import hass as hass

from custom_components.alorair_lite import local_coordinator as local
from custom_components.alorair_lite.const import CONF_LOCAL_POWER, DOMAIN
from custom_components.alorair_lite.local_client import (
    CommandNotSentError,
    CommandUncertainError,
    Config,
    DeviceStatus,
)


def sample(*, session=1, sequence=1, power=False, unit="fahrenheit", opcode=0x1C, age=0, received_at=None, target=None):
    return DeviceStatus(
        session,
        sequence,
        received_at or datetime.now(UTC),
        time.monotonic() - age,
        power,
        unit,
        opcode,
        target,
    )


class FakeClient:
    """Deliver explicit test reports through the real callback contract."""

    def __init__(self, config, on_status, on_disconnect):
        self.config = config
        self.on_status = on_status
        self.on_disconnect = on_disconnect
        self.connected = False
        self.last_status = None
        self.async_start = AsyncMock()
        self.async_close = AsyncMock(side_effect=self.close)
        self.async_set_power = AsyncMock(side_effect=self.power)
        self.async_set_temperature_unit = AsyncMock(side_effect=self.temperature)
        self.async_set_humidity = AsyncMock(side_effect=self.humidity)

    def close(self):
        self.connected = False
        self.last_status = None

    def publish(self, status):
        self.connected = True
        self.last_status = status
        self.on_status(status)

    def disconnect(self):
        session = self.last_status.session_id
        self.close()
        self.on_disconnect(session, "eof")

    async def power(self, on):
        previous = self.last_status
        result = sample(
            session=previous.session_id if previous else 1,
            sequence=previous.receive_sequence + 1 if previous else 1,
            power=on,
            unit=previous.temperature_unit if previous else "fahrenheit",
            opcode=0x21,
        )
        self.publish(result)
        return result

    async def temperature(self, unit):
        previous = self.last_status
        result = sample(
            session=previous.session_id,
            sequence=previous.receive_sequence + 1,
            power=previous.power,
            unit=unit,
            opcode=0x24,
        )
        self.publish(result)
        return result

    async def humidity(self, target):
        previous = self.last_status
        result = sample(
            session=previous.session_id,
            sequence=previous.receive_sequence + 1,
            power=previous.power,
            unit=previous.temperature_unit,
            opcode=0x23,
            target=target,
        )
        self.publish(result)
        return result


@pytest_asyncio.fixture
async def unit(hass, monkeypatch, request):
    monkeypatch.setattr(local, "LocalClient", FakeClient)
    monkeypatch.setattr(local, "WATCHDOG_INTERVAL", 0.01)
    entry = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Test local unit",
        data={"transport": "local", "mac": MAC},
        options={CONF_LOCAL_POWER: getattr(request, "param", False)},
        source="user",
        unique_id=MAC,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    coordinator = local.LocalAlorairCoordinator(hass, entry, Config("127.0.0.1", 0, "127.0.0.1", MAC))
    await coordinator.async_start()
    yield coordinator
    await coordinator.async_prepare_unload()


def allow_restart(unit):
    unit._last_off = time.monotonic() - 181


async def power(unit, on):
    await unit.async_command("async_set_power", (on,), "powerStatus", "01" if on else "00")


async def test_initial_listener_has_no_cloud_poll_or_invented_state(unit):
    assert unit.is_local and unit.data == {} and unit.status_stale
    assert not unit.last_update_success
    assert unit.update_interval is None
    unit.client.async_start.assert_awaited_once()
    await unit.async_request_refresh()
    assert not unit.last_update_success
    unit.client.publish(sample())
    await unit.async_request_refresh()
    assert unit.last_update_success
    assert set(unit.data) == {"powerStatus", "temperatureUnit", "currentHumidity", "updateTimeStr", "observed_at_utc"}
    assert unit.data["powerStatus"] == "00" and unit.data["temperatureUnit"] == 1
    assert not hasattr(unit.client, "async_status")


async def test_unknown_power_remains_unknown_and_receipt_age_uses_monotonic_time(unit):
    past_wall_time = datetime.now(UTC) - timedelta(days=10)
    unit.client.publish(sample(power=None, received_at=past_wall_time))
    assert unit.data["powerStatus"] is None
    assert unit.data["updateTimeStr"] == past_wall_time.isoformat()
    assert not unit.status_stale
    assert "errCode" not in unit.data and unit.data["currentHumidity"] is None


async def test_watchdog_marks_stale_and_recovers_without_polling(unit):
    unit.client.publish(sample())
    assert unit.last_update_success
    unit._received_monotonic -= 36
    await asyncio.sleep(0.03)
    assert unit.status_stale and not unit.last_update_success
    unit.client.publish(sample(sequence=2))
    assert not unit.status_stale and unit.last_update_success


async def test_disconnect_immediate_and_old_session_callbacks_cannot_restore_or_overwrite(unit):
    first = sample(power=True)
    unit.client.publish(first)
    unit.client.disconnect()
    assert not unit.last_update_success and unit.status_stale
    unit.client.on_status(first)
    assert not unit.last_update_success
    second = sample(session=2, sequence=1, power=False, unit="celsius")
    unit.client.publish(second)
    unit.client.on_status(sample(session=1, sequence=99, power=True))
    unit.client.on_disconnect(1, "eof")
    assert unit.last_update_success and not unit.status_stale
    assert unit.data["powerStatus"] == "00" and unit.data["temperatureUnit"] == 0
    unit.client.publish(sample(session=2, sequence=3, power=True))
    unit.client.on_status(sample(session=2, sequence=2, power=False))
    assert unit.data["powerStatus"] == "01"


async def test_power_start_disabled_by_default_but_off_remains_available(unit):
    unit.client.publish(sample(power=True))
    with pytest.raises(ServiceValidationError, match="Enable experimental local power"):
        await power(unit, True)
    await power(unit, False)
    unit.client.async_set_power.assert_awaited_once_with(False)
    assert unit.last_command.status == "device_reported"


@pytest.mark.parametrize("unit", [True], indirect=True)
async def test_start_requires_known_fresh_power_and_off_dwell(unit):
    unit.client.publish(sample(power=None))
    allow_restart(unit)
    with pytest.raises(ServiceValidationError, match="known local power"):
        await power(unit, True)
    unit.client.publish(sample(sequence=2))
    unit._last_off = time.monotonic()
    with pytest.raises(ServiceValidationError, match="restart protection"):
        await power(unit, True)
    allow_restart(unit)
    unit._received_monotonic -= 36
    with pytest.raises(ServiceValidationError, match="Fresh local"):
        await power(unit, True)
    unit.client.async_set_power.assert_not_awaited()
    unit.client.publish(sample(sequence=3))
    await power(unit, True)
    assert unit.data["powerStatus"] == "01"
    assert unit.last_command.status == "device_reported"
    assert unit.last_command.acknowledged
    assert unit.pending_power is None
    await power(unit, False)
    assert unit.restart_delay_remaining == 180


@pytest.mark.parametrize("unit", [True], indirect=True)
async def test_unknown_power_between_on_and_off_does_not_bypass_restart_dwell(unit):
    unit.client.publish(sample(power=True))
    allow_restart(unit)
    unit.client.publish(sample(sequence=2, power=None))
    assert unit.data["powerStatus"] is None
    unit.client.publish(sample(sequence=3, power=False))
    assert unit.data["powerStatus"] == "00"
    assert unit.restart_delay_remaining == 180
    with pytest.raises(ServiceValidationError, match="restart protection"):
        await power(unit, True)
    unit.client.async_set_power.assert_not_awaited()


async def test_off_restoration_uses_verified_session_even_with_stale_or_no_status(unit):
    unit.client.publish(sample(age=36))
    assert unit.status_stale
    await power(unit, False)
    unit.client.async_set_power.assert_awaited_once_with(False)
    unit.client.last_status = None
    unit.client.connected = True
    await power(unit, False)
    assert unit.client.async_set_power.await_count == 2
    unit.client.disconnect()
    with pytest.raises(ServiceValidationError, match="verified local"):
        await power(unit, False)


@pytest.mark.parametrize(
    ("method", "args", "key", "wanted"),
    [
        ("async_purge", (), "drainStatus", "01"),
        ("async_set_humidity", (51,), "currentHumidity", 51),
        ("async_set_humidity", (50,), "currentHumidity", 55),
        ("async_set_power", (1,), "powerStatus", "01"),
        ("async_set_power", (True,), "powerStatus", "00"),
        ("async_set_temperature_unit", ("celsius",), "temperatureUnit", "01"),
    ],
)
async def test_unsupported_commands_and_mismatched_intent_rejected(unit, method, args, key, wanted):
    unit.client.publish(sample())
    with pytest.raises(ServiceValidationError, match="not supported"):
        await unit.async_command(method, args, key, wanted)
    assert unit.last_command is None
    unit.client.async_set_power.assert_not_awaited()
    unit.client.async_set_temperature_unit.assert_not_awaited()


async def test_cloud_experimental_actions_rejected_without_client_call(unit):
    with pytest.raises(ServiceValidationError, match="cloud actions are unavailable"):
        await unit.async_experimental_action("firmware_check", {})


async def test_display_changes_work_while_off_and_returned_report_confirms(unit):
    unit.client.publish(sample())
    await unit.async_command("async_set_temperature_unit", ("celsius",), "temperatureUnit", "00")
    assert unit.data["temperatureUnit"] == 0
    assert unit.last_command.status == "device_reported"
    assert unit.last_command.reported_at is not None
    unit.client.async_set_temperature_unit.assert_awaited_once_with("celsius")


async def test_humidity_requires_on_even_without_caller_guard_and_tracks_auto_target(unit):
    unit.client.publish(sample(target=20))
    with pytest.raises(ServiceValidationError, match="Turn the dehumidifier on"):
        await unit.async_command("async_set_humidity", (50,), "currentHumidity", 50)
    unit.client.async_set_humidity.assert_not_awaited()
    unit.client.publish(sample(sequence=2, power=True, target=20))
    for target in (50, 55, 20):
        await unit.async_command("async_set_humidity", (target,), "currentHumidity", target)
        assert unit.data["currentHumidity"] == target
        assert unit.last_command.status == "device_reported"
        assert unit.last_command.requested == target
    assert unit.last_auto_humidity == 55
    unit.client.publish(sample(sequence=6, power=True, target=None))
    assert unit.data["currentHumidity"] is None
    assert unit.last_auto_humidity == 55


async def test_humidity_ack_does_not_overwrite_a_later_target_in_same_read(unit):
    unit.client.publish(sample(power=True, target=20))

    async def coalesced(target):
        ack = sample(sequence=2, power=True, opcode=0x23, target=target)
        latest = replace(ack, event_opcode=0x1C, target_humidity=55)
        unit.client.last_status = latest
        unit.client.on_status(ack)
        unit.client.on_status(latest)
        return ack

    unit.client.async_set_humidity.side_effect = coalesced
    await unit.async_command("async_set_humidity", (50,), "currentHumidity", 50)
    assert unit.last_command.status == "device_reported"
    assert unit.last_command.requested == 50
    assert unit.data["currentHumidity"] == 55


@pytest.mark.parametrize("unit", [True], indirect=True)
async def test_same_read_ack_does_not_overwrite_later_off_report_or_restart_dwell(unit):
    unit.client.publish(sample())
    allow_restart(unit)
    captured = {}

    async def coalesced(on):
        ack = sample(sequence=2, power=True, opcode=0x21)
        later_off = replace(ack, power=False, event_opcode=0x1C)
        captured.update(ack=ack, later_off=later_off)
        unit.client.last_status = later_off
        # Both queued callbacks share the receive counter and timestamp.
        unit.client.on_status(ack)
        assert unit.data["powerStatus"] == "00"
        unit.client.on_status(later_off)
        return ack

    unit.client.async_set_power.side_effect = coalesced
    await power(unit, True)
    assert unit.last_command.status == "device_reported"
    assert unit.last_command.requested == "01"
    assert unit.last_command.reported_at == captured["ack"].received_at
    assert unit.data["powerStatus"] == "00"
    assert unit.restart_delay_remaining == 180
    unit.client.on_status(captured["ack"])
    assert unit.data["powerStatus"] == "00"
    assert unit.last_command.status == "device_reported"
    assert unit.pending_power is None
    with pytest.raises(ServiceValidationError, match="restart protection"):
        await power(unit, True)
    unit.client.async_set_power.assert_awaited_once_with(True)


@pytest.mark.parametrize("unit", [True], indirect=True)
async def test_no_optimism_uncertain_result_not_replayed_or_confirmed_by_unqualified_callback(unit):
    unit.client.publish(sample())
    allow_restart(unit)
    entered, release = asyncio.Event(), asyncio.Event()

    async def uncertain(on):
        entered.set()
        await release.wait()
        raise CommandUncertainError("test")

    unit.client.async_set_power.side_effect = uncertain
    task = asyncio.create_task(power(unit, True))
    await entered.wait()
    assert unit.data["powerStatus"] == "00" and unit.pending_power == "on"
    assert unit.last_command.status == "awaiting_feedback"
    release.set()
    with pytest.raises(HomeAssistantError, match="uncertain"):
        await task
    unit.client.publish(sample(sequence=2, power=True, opcode=0x21))
    assert unit.last_command.status == "delivery_uncertain"
    assert unit.pending_power == "on"
    await power(unit, True)
    unit.client.async_set_power.assert_awaited_once()


@pytest.mark.parametrize("unit", [True], indirect=True)
async def test_pending_off_blocks_restart_even_if_latest_report_still_on(unit):
    unit.client.publish(sample(power=True))
    unit.client.async_set_power.side_effect = CommandUncertainError("test")
    with pytest.raises(HomeAssistantError):
        await power(unit, False)
    assert unit.pending_power == "off"
    with pytest.raises(ServiceValidationError, match="restart protection"):
        await power(unit, True)
    unit.client.async_set_power.assert_awaited_once_with(False)


async def test_not_sent_clears_pending_and_expiry_updates_uncertain_feedback(unit):
    unit.client.publish(sample(power=True))
    unit.client.async_set_power.side_effect = CommandNotSentError("fixed failure")
    with pytest.raises(HomeAssistantError, match="not sent"):
        await power(unit, False)
    assert not unit.pending and unit.last_command.status == "rejected"
    unit.client.async_set_power.side_effect = CommandUncertainError("test")
    with pytest.raises(HomeAssistantError, match="uncertain"):
        await power(unit, False)
    old = unit.pending["powerStatus"]
    unit.pending["powerStatus"] = local.LocalPendingCommand(old.wanted, time.monotonic() - 1, old.issued_monotonic)
    await asyncio.sleep(0.03)
    assert not unit.pending and unit.last_command.status == "not_observed"


@pytest.mark.parametrize("unit", [True], indirect=True)
async def test_cancelled_start_finishes_before_restoration_off(unit):
    unit.client.publish(sample())
    allow_restart(unit)
    entered, release = asyncio.Event(), asyncio.Event()
    order = []

    async def slow(on):
        order.append(on)
        if on:
            entered.set()
            await release.wait()
        return await unit.client.power(on)

    unit.client.async_set_power.side_effect = slow
    caller = asyncio.create_task(power(unit, True))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    restoration = asyncio.create_task(power(unit, False))
    await asyncio.sleep(0)
    assert order == [True]
    release.set()
    await restoration
    assert order == [True, False] and unit.data["powerStatus"] == "00"


async def test_unload_drains_submitted_work_ignores_new_reports_and_survives_repeated_cancellation(unit):
    unit.client.publish(sample(power=True))
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(on):
        entered.set()
        await release.wait()
        return await unit.client.power(on)

    unit.client.async_set_power.side_effect = slow
    command = asyncio.create_task(power(unit, False))
    await entered.wait()
    unload = asyncio.create_task(unit.async_prepare_unload())
    await asyncio.sleep(0)
    unload.cancel()
    await asyncio.sleep(0)
    unload.cancel()
    await asyncio.sleep(0)
    assert not unload.done()
    unit.client.async_close.assert_not_awaited()
    with pytest.raises(HomeAssistantError, match="unloading"):
        await power(unit, False)
    unit.client.on_status(sample(sequence=9, unit="celsius"))
    assert unit.data["temperatureUnit"] == 1
    release.set()
    await command
    assert await unload
    assert await unit.async_prepare_unload()
    unit.client.async_close.assert_awaited_once()
    assert unit._watchdog_task.done() and not unit._command_tasks


async def test_command_deadline_is_drained_before_close_and_never_replayed(unit, monkeypatch):
    unit.client.publish(sample(power=True))
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked(on):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(local, "COMMAND_TIMEOUT_SECONDS", 0.03)
    unit.client.async_set_power.side_effect = blocked
    command = asyncio.create_task(power(unit, False))
    await entered.wait()
    assert await unit.async_prepare_unload()
    assert cancelled.is_set()
    with pytest.raises(HomeAssistantError, match="uncertain"):
        await command
    assert unit.last_command.status == "delivery_uncertain"
    unit.client.async_set_power.assert_awaited_once_with(False)
    unit.client.async_close.assert_awaited_once()
