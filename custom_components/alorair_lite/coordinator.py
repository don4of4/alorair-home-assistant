"""Poll observed state and serialize commands without optimistic state changes."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EXPERIMENTAL_MUTATION_ACTIONS,
    AlorairClient,
    AlorairError,
    AuthenticationError,
    CannotConnect,
    CommandError,
)
from .const import (
    CONF_EXPERIMENTAL,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MINIMUM_OFF_SECONDS,
    PENDING_SECONDS,
)
from .models import code, faults, number, powered, vendor_datetime

_LOGGER = logging.getLogger(__name__)
COMMAND_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class PendingCommand:
    wanted: Any
    deadline: float
    issued_at: datetime
    baseline: datetime | None


@dataclass
class CommandFeedback:
    action: str
    status: str
    issued_at: datetime
    deadline: float
    field: str | None = None
    requested: str | int | None = None
    acknowledged: bool = False
    reported_at: datetime | None = None


def utc_sample(data: dict[str, Any]) -> datetime | None:
    sample = vendor_datetime(data)
    return sample.replace(tzinfo=UTC) if sample and sample.tzinfo is None else sample


class AlorairCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Share one polled state across all entities for a selected unit."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AlorairClient, mac: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
        )
        self.client = client
        self.mac = mac
        self.command_lock = asyncio.Lock()
        self.pending: dict[str, PendingCommand] = {}
        self._last_off = time.monotonic()
        self._previous_on: bool | None = None
        self.last_auto_humidity = 50
        self._closing = False
        self._command_tasks: set[asyncio.Task] = set()
        self.last_command: CommandFeedback | None = None

    async def async_prepare_unload(self) -> bool:
        """Drain bounded command work before the entry can be replaced."""
        self._closing = True
        active = [task for task in self._command_tasks if not task.done()]
        if active:
            # Each active transaction has an end-to-end deadline, including
            # discovery/authentication and feedback. Queued commands see closing.
            await asyncio.gather(*active, return_exceptions=True)
        return True

    @property
    def status_stale(self) -> bool:
        if not self.data:
            return True
        sampled = utc_sample(self.data)
        if sampled is None:
            return True
        # The app's string-date conversion treats naive samples as UTC.
        # Firmware cadence/timezone must be checked during live commissioning.
        age = (datetime.now(UTC) - sampled).total_seconds()
        return age < -60 or age > 300

    @property
    def restart_delay_remaining(self) -> int:
        if self.pending_power == "off":
            return MINIMUM_OFF_SECONDS
        if self.data and powered(self.data) is True:
            return 0
        return max(0, int(MINIMUM_OFF_SECONDS - (time.monotonic() - self._last_off) + 0.999))

    @property
    def pending_power(self) -> str | None:
        item = self.pending.get("powerStatus")
        if item and item.deadline > time.monotonic():
            return "off" if item.wanted == "00" else "on"
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.async_status(self.mac)
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("ALORAIR login expired or was rejected") from err
        except AlorairError as err:
            raise UpdateFailed("ALORAIR status could not be read") from err
        on = powered(data)
        if on is None:
            raise UpdateFailed("ALORAIR returned an unsupported power status")
        if on is False and self._previous_on is True:
            self._last_off = time.monotonic()
        self._previous_on = on
        target = number(data, "currentHumidity", 25, 80)
        if target is not None and target % 5 == 0:
            self.last_auto_humidity = int(target)
        now = time.monotonic()
        sampled = utc_sample(data)
        for key, pending in list(self.pending.items()):
            wanted = pending.wanted
            actual = code(data, key) if key != "currentHumidity" else number(data, key)
            matches = actual == wanted or (key == "powerStatus" and wanted == "01" and on)
            newer = (
                sampled is not None
                and sampled >= pending.issued_at
                and (pending.baseline is None or sampled > pending.baseline)
            )
            if matches and newer:
                self.pending.pop(key, None)
                if (
                    self.last_command
                    and self.last_command.field == key
                    and self.last_command.issued_at == pending.issued_at
                ):
                    self.last_command.status = "device_reported"
                    self.last_command.reported_at = sampled
                if key == "powerStatus" and wanted == "00":
                    self._last_off = now
            elif now >= pending.deadline:
                self.pending.pop(key, None)
                if (
                    self.last_command
                    and self.last_command.field == key
                    and self.last_command.issued_at == pending.issued_at
                ):
                    self.last_command.status = "not_observed"
        return data

    async def async_command(
        self, method: str, args: tuple[Any, ...], key: str, wanted: Any, *, requires_on: bool = False
    ) -> None:
        """Keep an outgoing command serialized even if its calling automation is cancelled."""
        if self._closing:
            raise HomeAssistantError("ALORAIR is unloading; wait for reload to finish")
        task = self.config_entry.async_create_task(
            self.hass, self._async_command(method, args, key, wanted, requires_on), f"{DOMAIN} device command"
        )
        self._command_tasks.add(task)
        task.add_done_callback(self._command_tasks.discard)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise

    async def async_experimental_action(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Run an opted-in action with the same unload and transaction bounds."""
        if not self.config_entry.options.get(CONF_EXPERIMENTAL, False):
            raise ServiceValidationError("Enable experimental controls in the integration options first")
        if self._closing:
            raise HomeAssistantError("ALORAIR is unloading; wait for reload to finish")
        task = self.config_entry.async_create_task(
            self.hass, self._async_experimental_action(action, parameters), f"{DOMAIN} experimental action"
        )
        self._command_tasks.add(task)
        task.add_done_callback(self._command_tasks.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise

    async def _async_experimental_action(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        async with self.command_lock:
            try:
                async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
                    return await self._async_experimental_transaction(action, parameters)
            except TimeoutError as err:
                if action not in EXPERIMENTAL_MUTATION_ACTIONS:
                    raise HomeAssistantError("Experimental data request deadline expired") from err
                if self.last_command and self.last_command.action == action:
                    self.last_command.status = "delivery_uncertain"
                    self.async_update_listeners()
                raise HomeAssistantError("Experimental action deadline expired; it was not retried") from err

    async def _async_experimental_transaction(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        writes = EXPERIMENTAL_MUTATION_ACTIONS
        if self._closing:
            raise HomeAssistantError("ALORAIR is unloading; the queued action was not sent")
        if action in writes and (not self.last_update_success or self.status_stale):
            raise ServiceValidationError("Fresh device status is required before an experimental change")
        feedback = None
        if action in writes:
            feedback = self.last_command = CommandFeedback(
                action, "awaiting_response", datetime.now(UTC), time.monotonic() + PENDING_SECONDS
            )
            self.async_update_listeners()
        try:
            result = await self.client.async_experimental_action(self.mac, action, **parameters)
        except AuthenticationError as err:
            if feedback:
                feedback.status = "rejected"
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError("ALORAIR authentication must be renewed") from err
        except (AlorairError, TimeoutError) as err:
            if action not in writes:
                raise HomeAssistantError(
                    "ALORAIR cloud data could not be retrieved; the request failed or timed out"
                    if isinstance(err, (CannotConnect, TimeoutError))
                    else "ALORAIR rejected the data request or returned an unsupported response"
                ) from err
            uncertain = isinstance(err, (CannotConnect, TimeoutError)) or (
                isinstance(err, CommandError) and err.outcome_unknown
            )
            if feedback:
                feedback.status = "delivery_uncertain" if uncertain else "rejected"
            raise HomeAssistantError(
                "Experimental action outcome is uncertain; it was not retried"
                if uncertain
                else "ALORAIR rejected the experimental action or returned an unsupported response"
            ) from err
        else:
            if feedback:
                feedback.acknowledged = True
                feedback.status = "cloud_acknowledged"
                await self.async_request_refresh()
            return {"action": action, "status": "cloud_acknowledged" if feedback else "read", "result": result}
        finally:
            self.async_update_listeners()

    async def _async_command(
        self, method: str, args: tuple[Any, ...], key: str, wanted: Any, requires_on: bool
    ) -> None:
        async with self.command_lock:
            try:
                await self._async_command_with_feedback(method, args, key, wanted, requires_on)
            except TimeoutError as err:
                if self.last_command and self.last_command.action == method.removeprefix("async_"):
                    self.last_command.status = "delivery_uncertain"
                # The API propagates cancellation, so this task cannot later send
                # a request. An already submitted POST still has an unknown outcome.
                if not self._closing:
                    self.config_entry.async_create_background_task(
                        self.hass, self.async_request_refresh(), f"{DOMAIN} timeout feedback"
                    )
                self.async_update_listeners()
                raise HomeAssistantError(
                    "ALORAIR command deadline expired; delivery is uncertain and the command was not retried"
                ) from err

    async def _async_command_with_feedback(
        self, method: str, args: tuple[Any, ...], key: str, wanted: Any, requires_on: bool
    ) -> None:
        async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
            if self._closing:
                raise HomeAssistantError("ALORAIR is unloading; the queued command was not sent")
            if not self.last_update_success or not self.data or self.status_stale:
                raise HomeAssistantError("ALORAIR status is unavailable; wait for reconnection")
            pending = self.pending.get(key)
            if pending and pending.deadline > time.monotonic() and pending.wanted == wanted:
                return
            actual = code(self.data, key) if key != "currentHumidity" else number(self.data, key)
            if actual == wanted and not pending:
                return
            if key == "powerStatus" and wanted == "01":
                if powered(self.data) is True and not pending:
                    return
                if self.restart_delay_remaining:
                    raise ServiceValidationError(
                        "Compressor restart protection is active; wait three minutes after off or reload"
                    )
            if requires_on and powered(self.data) is not True:
                raise ServiceValidationError("Turn the dehumidifier on before changing this setting")
            if not (key == "powerStatus" and wanted == "00") and faults(self.data) != []:
                raise ServiceValidationError("Resolve the dehumidifier fault before sending this command")
            self.pending[key] = PendingCommand(
                wanted, time.monotonic() + PENDING_SECONDS, datetime.now(UTC), utc_sample(self.data)
            )
            self.last_command = feedback = CommandFeedback(
                method.removeprefix("async_"),
                "awaiting_feedback",
                self.pending[key].issued_at,
                self.pending[key].deadline,
                key,
                wanted,
            )
            if key == "powerStatus" and wanted == "00":
                self._last_off = time.monotonic()
            self.async_update_listeners()
            try:
                await getattr(self.client, method)(self.mac, *args)
            except AuthenticationError as err:
                feedback.status = "rejected"
                self.pending.pop(key, None)
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError("ALORAIR authentication must be renewed") from err
            except CannotConnect as err:
                feedback.status = "delivery_uncertain"
                # Delivery may have succeeded. Retain intent and poll; never immediately replay.
                await self.async_request_refresh()
                raise HomeAssistantError(
                    "Command delivery is uncertain; checking device feedback before retrying"
                ) from err
            except CommandError as err:
                feedback.status = "delivery_uncertain" if err.outcome_unknown else "rejected"
                if not err.outcome_unknown:
                    self.pending.pop(key, None)
                else:
                    await self.async_request_refresh()
                raise HomeAssistantError(
                    "Command delivery is uncertain; checking device feedback before retrying"
                    if err.outcome_unknown
                    else "ALORAIR rejected the command"
                ) from err
            except AlorairError as err:
                feedback.status = "rejected"
                self.pending.pop(key, None)
                raise HomeAssistantError("ALORAIR rejected the command") from err
            finally:
                self.async_update_listeners()
            feedback.acknowledged = True
            await self.async_request_refresh()
