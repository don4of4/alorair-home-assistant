"""Exercise opt-in services, transaction lifecycle and retained feedback in real HA."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from test_integration import MAC, entry, status
from test_integration import hass as hass

from custom_components.alorair_lite.api import CannotConnect, CommandError, ProtocolError
from custom_components.alorair_lite.const import CONF_EXPERIMENTAL, CONF_POLL_INTERVAL, DOMAIN
from custom_components.alorair_lite.services import SERVICE_EXPERIMENTAL


@pytest_asyncio.fixture
async def unit(hass, request):
    original = entry()
    configured = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title=original.title,
        data=original.data,
        options={CONF_EXPERIMENTAL: getattr(request, "param", True)},
        source="user",
        unique_id=MAC,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    data = status(powerStatus="01", updateTimeStr=(datetime.now(UTC) - timedelta(seconds=2)).isoformat())
    client = AsyncMock()
    client.async_status.side_effect = lambda mac: data.copy()
    client.async_experimental_action.return_value = {"items": []}
    with patch("custom_components.alorair_lite.AlorairClient", return_value=client):
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
        assert configured.state is config_entries.ConfigEntryState.LOADED
        device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, MAC)})
        assert device is not None
        registry = er.async_get(hass)
        feedback_id = registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_last_command")
        purge_id = registry.async_get_entity_id("button", DOMAIN, f"{MAC}_purge")
        assert feedback_id and purge_id
        assert hass.states.get(feedback_id).state == "none"
        yield SimpleNamespace(
            entry=configured,
            coordinator=configured.runtime_data,
            client=client,
            data=data,
            device=device,
            feedback_id=feedback_id,
            purge_id=purge_id,
        )


async def call_action(hass, unit, action, **parameters):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_EXPERIMENTAL,
        {"device_id": unit.device.id, "action": action, **parameters},
        blocking=True,
        return_response=True,
    )


@pytest.mark.parametrize("action", ["history", "operation_time"])
async def test_registered_read_service_returns_response_and_date_defaults(hass, unit, action):
    fixed_now = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)
    with patch("custom_components.alorair_lite.services.dt_util.now", return_value=fixed_now):
        result = await call_action(hass, unit, action)
    assert result == {"action": action, "status": "read", "result": {"items": []}}
    unit.client.async_experimental_action.assert_awaited_once_with(MAC, action, period="day", query_date="2026-01-01")
    assert hass.states.get(unit.feedback_id).state == "none"


@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("history", {"period": "month", "query_date": "2026-08-01"}),
        ("operation_time", {"period": "year", "query_date": "2026-01-01"}),
        ("filters", {}),
        ("filter_detail", {"filter_id": "owned-filter"}),
        ("firmware_check", {}),
        ("firmware_history", {"page": 2}),
        ("rename", {"label": "Test device"}),
        ("location", {"label": "Test location"}),
        ("extend_filter", {"filter_id": "owned-filter", "extension": 3}),
        ("reset_filter", {"filter_id": "owned-filter"}),
    ],
)
async def test_registered_service_routes_allowed_actions_and_parameters(hass, unit, action, parameters):
    result = await call_action(hass, unit, action, **parameters)
    assert result["action"] == action
    unit.client.async_experimental_action.assert_awaited_once_with(MAC, action, **parameters)


@pytest.mark.parametrize(
    "parameters",
    [
        {"action": "upgrade_firmware"},
        {"action": "history", "period": "week"},
        {"action": "firmware_history", "page": 0},
        {"action": "firmware_history", "page": 11},
        {"action": "extend_filter", "extension": 0},
        {"action": "extend_filter", "extension": 4},
        {"action": "filters", "deviceNum": "ANOTHER_DEVICE"},
    ],
)
async def test_registered_service_rejects_unsupported_or_unbounded_parameters(hass, unit, parameters):
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPERIMENTAL,
            {"device_id": unit.device.id, **parameters},
            blocking=True,
            return_response=True,
        )
    unit.client.async_experimental_action.assert_not_awaited()


@pytest.mark.parametrize("unit", [False], indirect=True)
async def test_disabled_option_blocks_both_registered_service_and_direct_coordinator(hass, unit):
    with pytest.raises(ServiceValidationError, match="Enable experimental controls"):
        await call_action(hass, unit, "filters")
    with pytest.raises(ServiceValidationError, match="Enable experimental controls"):
        await unit.coordinator.async_experimental_action("rename", {"label": "Test device"})
    unit.client.async_experimental_action.assert_not_awaited()
    assert hass.states.get(unit.feedback_id).state == "none"


@pytest.mark.parametrize("unit", [False], indirect=True)
async def test_options_flow_enables_service_then_disabling_removes_access(hass, unit):
    flow = await hass.config_entries.options.async_init(unit.entry.entry_id)
    assert flow["type"] == "form"
    assert flow["data_schema"]({CONF_POLL_INTERVAL: 30})[CONF_EXPERIMENTAL] is False
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], user_input={CONF_POLL_INTERVAL: 30, CONF_EXPERIMENTAL: True}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert unit.entry.options[CONF_EXPERIMENTAL] is True
    await call_action(hass, unit, "filters")
    unit.client.async_experimental_action.assert_awaited_once_with(MAC, "filters")
    flow = await hass.config_entries.options.async_init(unit.entry.entry_id)
    await hass.config_entries.options.async_configure(
        flow["flow_id"], user_input={CONF_POLL_INTERVAL: 30, CONF_EXPERIMENTAL: False}
    )
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError, match="Enable experimental controls"):
        await call_action(hass, unit, "filters")
    unit.client.async_experimental_action.assert_awaited_once()


async def test_device_selector_requires_registered_device_id_not_mac(hass, unit):
    with pytest.raises(ServiceValidationError, match="registered in Home Assistant"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPERIMENTAL,
            {"device_id": MAC, "action": "filters"},
            blocking=True,
            return_response=True,
        )
    unit.client.async_experimental_action.assert_not_awaited()


@pytest.mark.parametrize("identifier", [("other_integration", "foreign-device"), (DOMAIN, "AABBCCDDEE02")])
async def test_foreign_registry_device_cannot_select_units_entry(hass, unit, identifier):
    foreign = dr.async_get(hass).async_get_or_create(config_entry_id=unit.entry.entry_id, identifiers={identifier})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPERIMENTAL,
            {"device_id": foreign.id, "action": "filters"},
            blocking=True,
            return_response=True,
        )
    unit.client.async_experimental_action.assert_not_awaited()


async def test_registered_but_unloaded_device_rejects_action(hass, unit):
    assert await hass.config_entries.async_unload(unit.entry.entry_id)
    assert dr.async_get(hass).async_get(unit.device.id) is not None
    with pytest.raises(ServiceValidationError, match="loaded integration entry"):
        await call_action(hass, unit, "filters")
    unit.client.async_experimental_action.assert_not_awaited()


async def test_last_command_retains_only_fixed_labels_for_experimental_writes(hass, unit):
    private_label = "PRIVATE TEST LABEL - no household data"
    unit.client.async_experimental_action.return_value = {"name": private_label}
    result = await call_action(hass, unit, "rename", label=private_label)
    assert result["status"] == "cloud_acknowledged"
    feedback = hass.states.get(unit.feedback_id)
    assert feedback.state == "cloud_acknowledged"
    assert feedback.attributes["action"] == "rename"
    assert feedback.attributes["requested_value"] is None
    assert feedback.attributes["cloud_acknowledged"] is True
    assert feedback.attributes["device_reported_at"] is None
    assert private_label not in str(feedback.as_dict())
    await call_action(hass, unit, "filters")
    assert hass.states.get(unit.feedback_id).state == "cloud_acknowledged"
    assert hass.states.get(unit.feedback_id).attributes["action"] == "rename"


@pytest.mark.parametrize("failure", ["transport", "ambiguous_response"])
async def test_experimental_uncertain_delivery_is_not_retried(hass, unit, failure):
    unit.client.async_experimental_action.side_effect = (
        CannotConnect("TEST transport failure")
        if failure == "transport"
        else CommandError("TEST", outcome_unknown=True)
    )
    with pytest.raises(HomeAssistantError, match="uncertain; it was not retried"):
        await call_action(hass, unit, "location", label="Test device")
    feedback = hass.states.get(unit.feedback_id)
    assert feedback.state == "delivery_uncertain"
    assert feedback.attributes["cloud_acknowledged"] is False
    unit.client.async_experimental_action.assert_awaited_once()


@pytest.mark.parametrize("action", ["history", "operation_time"])
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (CannotConnect, "cloud data could not be retrieved"),
        (TimeoutError, "cloud data could not be retrieved"),
        (ProtocolError, "rejected the data request"),
    ],
)
async def test_failed_data_reads_preserve_command_feedback_and_use_read_errors(hass, unit, action, failure, message):
    await call_action(hass, unit, "location", label="Test location")
    before = hass.states.get(unit.feedback_id)
    unit.client.async_experimental_action.reset_mock()
    unit.client.async_experimental_action.side_effect = failure("PRIVATE request details")

    with pytest.raises(HomeAssistantError, match=message) as raised:
        await call_action(hass, unit, action)

    assert "PRIVATE" not in str(raised.value)
    assert "uncertain" not in str(raised.value)
    assert "delivery" not in str(raised.value)
    assert hass.states.get(unit.feedback_id).state == before.state
    assert hass.states.get(unit.feedback_id).attributes == before.attributes
    unit.client.async_experimental_action.assert_awaited_once()


async def test_data_read_deadline_preserves_command_feedback(hass, unit):
    await call_action(hass, unit, "location", label="Test location")
    before = hass.states.get(unit.feedback_id)
    unit.client.async_experimental_action.reset_mock()
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    unit.client.async_experimental_action.side_effect = stalled
    with patch("custom_components.alorair_lite.coordinator.COMMAND_TIMEOUT_SECONDS", 0.03):
        async with asyncio.timeout(2):
            with pytest.raises(HomeAssistantError, match="data request deadline expired"):
                await call_action(hass, unit, "history")

    assert cancelled.is_set()
    assert hass.states.get(unit.feedback_id).state == before.state
    assert hass.states.get(unit.feedback_id).attributes == before.attributes
    assert not unit.coordinator._command_tasks
    unit.client.async_experimental_action.assert_awaited_once()


async def test_experimental_explicit_rejection_is_retained(hass, unit):
    unit.client.async_experimental_action.side_effect = CommandError("PRIVATE raw server rejection")
    with pytest.raises(HomeAssistantError, match="rejected the experimental action"):
        await call_action(hass, unit, "reset_filter", filter_id="owned-filter")
    feedback = hass.states.get(unit.feedback_id)
    assert feedback.state == "rejected"
    assert "PRIVATE" not in str(feedback.as_dict())
    unit.client.async_experimental_action.assert_awaited_once()


async def test_experimental_timeout_is_bounded_and_marks_uncertainty(hass, unit):
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    unit.client.async_experimental_action.side_effect = stalled
    with patch("custom_components.alorair_lite.coordinator.COMMAND_TIMEOUT_SECONDS", 0.03):
        async with asyncio.timeout(2):
            with pytest.raises(HomeAssistantError, match="deadline expired"):
                await call_action(hass, unit, "rename", label="Test device")
    assert cancelled.is_set()
    assert hass.states.get(unit.feedback_id).state == "delivery_uncertain"
    unit.client.async_experimental_action.assert_awaited_once()
    assert not unit.coordinator._command_tasks


async def test_cancelled_caller_and_unload_drain_active_experimental_write(hass, unit):
    started, release, finished, unloading = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_prepare = unit.coordinator.async_prepare_unload

    async def prepare_unload():
        unloading.set()
        return await original_prepare()

    async def send(*args, **kwargs):
        started.set()
        await release.wait()
        finished.set()
        return {"ok": True}

    unit.client.async_experimental_action.side_effect = send
    caller = asyncio.create_task(unit.coordinator.async_experimental_action("rename", {"label": "Test device"}))
    unload = None
    try:
        async with asyncio.timeout(2):
            await started.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            with patch.object(unit.coordinator, "async_prepare_unload", side_effect=prepare_unload):
                unload = asyncio.create_task(hass.config_entries.async_unload(unit.entry.entry_id))
                await unloading.wait()
                assert unit.coordinator._closing
                assert not unload.done()
                assert not finished.is_set()
                with pytest.raises(HomeAssistantError, match="unloading"):
                    await unit.coordinator.async_experimental_action("location", {"label": "Test device"})
                release.set()
                assert await unload
    finally:
        release.set()
        await asyncio.gather(caller, *([unload] if unload else []), return_exceptions=True)
    assert finished.is_set()
    assert unit.entry.state is config_entries.ConfigEntryState.NOT_LOADED
    assert not unit.coordinator._command_tasks
    unit.client.async_experimental_action.assert_awaited_once_with(MAC, "rename", label="Test device")


async def test_cancelled_caller_still_records_experimental_timeout_before_unload(hass, unit):
    started = asyncio.Event()

    async def stalled(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    unit.client.async_experimental_action.side_effect = stalled
    with patch("custom_components.alorair_lite.coordinator.COMMAND_TIMEOUT_SECONDS", 0.03):
        async with asyncio.timeout(2):
            caller = asyncio.create_task(unit.coordinator.async_experimental_action("rename", {"label": "Test device"}))
            await started.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert await hass.config_entries.async_unload(unit.entry.entry_id)
    assert unit.coordinator.last_command.status == "delivery_uncertain"
    assert not unit.coordinator._command_tasks
    unit.client.async_experimental_action.assert_awaited_once()


async def test_stale_samples_block_experimental_writes_but_allow_reads(hass, unit):
    unit.data["updateTimeStr"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await unit.coordinator.async_refresh()
    with pytest.raises(ServiceValidationError, match="Fresh device status"):
        await call_action(hass, unit, "extend_filter", filter_id="owned-filter", extension=1)
    unit.client.async_experimental_action.assert_not_awaited()
    assert (await call_action(hass, unit, "filters"))["status"] == "read"


async def test_purge_feedback_requires_new_sample_and_retains_transient_report(hass, unit):
    await hass.services.async_call("button", "press", {"entity_id": unit.purge_id}, blocking=True)
    unit.client.async_purge.assert_awaited_once_with(MAC)
    feedback = hass.states.get(unit.feedback_id)
    assert feedback.state == "awaiting_feedback"
    assert feedback.attributes["cloud_acknowledged"] is True
    issued_at = feedback.attributes["requested_at"]

    # The cache now contains the requested value, but its sample predates this command.
    unit.data["drainStatus"] = "01"
    await unit.coordinator.async_refresh()
    assert hass.states.get(unit.feedback_id).state == "awaiting_feedback"
    assert "drainStatus" in unit.coordinator.pending

    sampled = max(datetime.now(UTC), issued_at + timedelta(milliseconds=1))
    unit.data["updateTimeStr"] = sampled.isoformat()
    await unit.coordinator.async_refresh()
    feedback = hass.states.get(unit.feedback_id)
    assert feedback.state == "device_reported"
    assert feedback.attributes["device_reported_at"] == sampled
    assert "drainStatus" not in unit.coordinator.pending

    unit.data.update(drainStatus="00", updateTimeStr=(sampled + timedelta(seconds=1)).isoformat())
    await unit.coordinator.async_refresh()
    feedback = hass.states.get(unit.feedback_id)
    assert feedback.state == "device_reported"
    assert feedback.attributes["action"] == "purge"
    assert feedback.attributes["device_reported_at"] == sampled
    unit.client.async_purge.assert_awaited_once()


async def test_uncertain_command_can_be_confirmed_by_later_fresh_report_without_replay(hass, unit):
    unit.client.async_purge.side_effect = CannotConnect("TEST response lost after send")
    with pytest.raises(HomeAssistantError, match="uncertain"):
        await hass.services.async_call("button", "press", {"entity_id": unit.purge_id}, blocking=True)
    assert hass.states.get(unit.feedback_id).state == "delivery_uncertain"
    # A repeated press while outcome is pending must not resend the physical command.
    await hass.services.async_call("button", "press", {"entity_id": unit.purge_id}, blocking=True)
    unit.client.async_purge.assert_awaited_once_with(MAC)
    unit.data.update(drainStatus="01", updateTimeStr=datetime.now(UTC).isoformat())
    await unit.coordinator.async_refresh()
    feedback = hass.states.get(unit.feedback_id)
    assert feedback.state == "device_reported"
    assert feedback.attributes["cloud_acknowledged"] is False
    assert feedback.attributes["device_reported_at"] is not None
    unit.client.async_purge.assert_awaited_once()


async def test_precommand_sample_in_same_second_cannot_confirm_delivery(hass, unit):
    issued_at = datetime.now(UTC).replace(microsecond=800000)
    unit.data["updateTimeStr"] = (issued_at - timedelta(seconds=2)).isoformat()
    await unit.coordinator.async_refresh()
    with patch("custom_components.alorair_lite.coordinator.datetime", wraps=datetime) as clock:
        clock.now.return_value = issued_at
        await hass.services.async_call("button", "press", {"entity_id": unit.purge_id}, blocking=True)
    assert hass.states.get(unit.feedback_id).attributes["requested_at"] == issued_at

    # Newer than our old cache, but explicitly 700 ms before this command was issued.
    unit.data.update(drainStatus="01", updateTimeStr=issued_at.replace(microsecond=100000).isoformat())
    await unit.coordinator.async_refresh()
    assert hass.states.get(unit.feedback_id).state == "awaiting_feedback"
    assert hass.states.get(unit.feedback_id).attributes["device_reported_at"] is None

    unit.data["updateTimeStr"] = (issued_at + timedelta(seconds=1)).isoformat()
    await unit.coordinator.async_refresh()
    assert hass.states.get(unit.feedback_id).state == "device_reported"
    unit.client.async_purge.assert_awaited_once()
