"""Receive verified local reports and serialize explicitly enabled controls."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_LOCAL_POWER, DOMAIN, MINIMUM_OFF_SECONDS, PENDING_SECONDS
from .local_client import (
    CommandUncertainError,
    Config,
    DeviceStatus,
    LocalClient,
    LocalClientError,
)

_LOGGER = logging.getLogger(__name__)
STATUS_MAX_AGE = 35.0
WATCHDOG_INTERVAL = 1.0
COMMAND_TIMEOUT_SECONDS = 30.0


@dataclass
class LocalCommandFeedback:
    action: str
    status: str
    issued_at: datetime
    deadline: float
    field: str
    requested: str | int
    acknowledged: bool = False
    reported_at: datetime | None = None


@dataclass(frozen=True)
class LocalPendingCommand:
    wanted: str | int
    deadline: float
    issued_monotonic: float


class LocalAlorairCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Expose observed controls and target state without cloud polling."""

    is_local = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, config: Config) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.mac = config.mac
        self.data = {}
        self.last_update_success = False
        self.client = LocalClient(config, on_status=self._on_status, on_disconnect=self._on_disconnect)
        self.command_lock = asyncio.Lock()
        self.pending: dict[str, LocalPendingCommand] = {}
        self.last_command: LocalCommandFeedback | None = None
        self.last_auto_humidity = 50
        self._session_id: int | None = None
        self._receive_sequence = -1
        self._received_monotonic: float | None = None
        self._previous_on: bool | None = None
        self._last_off = time.monotonic()
        self._closing = False
        self._command_tasks: set[asyncio.Task] = set()
        self._watchdog_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None

    async def async_start(self) -> None:
        """Bind the listener; an initial device connection is not required."""
        try:
            await self.client.async_start()
        except BaseException:
            await self.client.async_close()
            raise
        self._watchdog_task = self.config_entry.async_create_background_task(
            self.hass, self._watchdog(), f"{DOMAIN} local freshness"
        )

    @property
    def status_stale(self) -> bool:
        current = self.client.last_status
        return (
            self._closing
            or not self.client.connected
            or current is None
            or current.session_id != self._session_id
            or self._received_monotonic is None
            or not 0 <= time.monotonic() - self._received_monotonic <= STATUS_MAX_AGE
        )

    @property
    def pending_power(self) -> str | None:
        pending = self.pending.get("powerStatus")
        if pending and pending.deadline > time.monotonic():
            return "off" if pending.wanted == "00" else "on"
        return None

    @property
    def restart_delay_remaining(self) -> int:
        if self.pending_power == "off":
            return MINIMUM_OFF_SECONDS
        if self.data.get("powerStatus") == "01" and not self.status_stale:
            return 0
        return max(0, int(MINIMUM_OFF_SECONDS - (time.monotonic() - self._last_off) + 0.999))

    @callback
    def _on_status(self, status: DeviceStatus) -> None:
        current = self.client.last_status
        if (
            self._closing
            or not self.client.connected
            or current is None
            or current.session_id != status.session_id
            or (self._session_id is not None and status.session_id < self._session_id)
            or (
                current.receive_sequence == status.receive_sequence
                and current.received_monotonic == status.received_monotonic
                and current != status
            )
        ):
            return
        if status.session_id != self._session_id:
            self._session_id = status.session_id
            self._receive_sequence = -1
            self._received_monotonic = None
            self._previous_on = None
            self._last_off = time.monotonic()
        if status.receive_sequence < self._receive_sequence or (
            self._received_monotonic is not None and status.received_monotonic < self._received_monotonic
        ):
            return
        if status.power is False and self._previous_on is not False:
            self._last_off = time.monotonic()
        self._previous_on = status.power
        self._receive_sequence = status.receive_sequence
        self._received_monotonic = status.received_monotonic
        received_at = status.received_at.astimezone(UTC).isoformat()
        if status.target_humidity is not None and status.target_humidity >= 25:
            self.last_auto_humidity = status.target_humidity
        self.async_set_updated_data(
            {
                "powerStatus": "01" if status.power is True else "00" if status.power is False else None,
                "temperatureUnit": {"celsius": 0, "fahrenheit": 1}.get(status.temperature_unit),
                "currentHumidity": status.target_humidity,
                "updateTimeStr": received_at,
                "observed_at_utc": received_at,
            }
        )
        if self.status_stale:
            self.async_set_update_error(UpdateFailed("Local device status is stale"))

    @callback
    def _on_disconnect(self, session_id: int, reason: str) -> None:
        if self._closing or self._session_id != session_id:
            return
        self._received_monotonic = None
        self._previous_on = None
        self._last_off = time.monotonic()
        self.async_set_update_error(UpdateFailed("Local device disconnected"))

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            now = time.monotonic()
            changed = False
            for key, pending in list(self.pending.items()):
                if now >= pending.deadline:
                    self.pending.pop(key)
                    if self.last_command and self.last_command.field == key:
                        self.last_command.status = "not_observed"
                    changed = True
            if self.status_stale and self.last_update_success:
                self.async_set_update_error(UpdateFailed("Local device status is stale"))
            elif changed:
                self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh HA's view of received data without sending a device request."""
        if self.status_stale:
            raise UpdateFailed("No fresh local device report is available")
        return self.data

    async def async_experimental_action(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        raise ServiceValidationError("Experimental cloud actions are unavailable with the local transport")

    @staticmethod
    def _validate_command(method: str, args: tuple[Any, ...], key: str, wanted: Any) -> int:
        if method == "async_set_power" and len(args) == 1 and type(args[0]) is bool:
            if key == "powerStatus" and wanted == ("01" if args[0] else "00"):
                return 0x21
        if method == "async_set_temperature_unit" and len(args) == 1 and args[0] in ("celsius", "fahrenheit"):
            if key == "temperatureUnit" and wanted == ("01" if args[0] == "fahrenheit" else "00"):
                return 0x24
        if method == "async_set_humidity" and len(args) == 1 and type(args[0]) is int:
            if (
                key == "currentHumidity"
                and type(wanted) is int
                and wanted == args[0]
                and (wanted == 20 or 25 <= wanted <= 80 and wanted % 5 == 0)
            ):
                return 0x23
        raise ServiceValidationError("This local command or parameter is not supported")

    async def async_command(
        self, method: str, args: tuple[Any, ...], key: str, wanted: Any, *, requires_on: bool = False
    ) -> None:
        opcode = self._validate_command(method, args, key, wanted)
        if self._closing:
            raise HomeAssistantError("ALORAIR is unloading; wait for reload to finish")
        task = self.config_entry.async_create_task(
            self.hass,
            self._async_command(method, args, key, wanted, opcode, requires_on),
            f"{DOMAIN} local device command",
        )
        self._command_tasks.add(task)
        task.add_done_callback(self._command_tasks.discard)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise

    async def _async_command(self, method, args, key, wanted, opcode, requires_on) -> None:
        async with self.command_lock:
            if self._closing:
                raise HomeAssistantError("ALORAIR is unloading; the queued command was not sent")
            if not self.client.connected:
                raise ServiceValidationError("A verified local device connection is required")
            starting = opcode == 0x21 and wanted == "01"
            stopping = opcode == 0x21 and wanted == "00"
            if starting and not self.config_entry.options.get(CONF_LOCAL_POWER, False):
                raise ServiceValidationError("Enable experimental local power in the integration options first")
            if not stopping and self.status_stale:
                raise ServiceValidationError("Fresh local device status is required before this command")
            power = self.data.get("powerStatus")
            if starting and power not in {"00", "01"}:
                raise ServiceValidationError("A known local power state is required before starting")
            if (requires_on or opcode == 0x23) and power != "01":
                raise ServiceValidationError("Turn the dehumidifier on before changing this setting")
            pending = self.pending.get(key)
            if pending and pending.deadline > time.monotonic() and pending.wanted == wanted:
                raise ServiceValidationError(
                    "The previous request's delivery is uncertain; no retry was sent. "
                    "Wait for the pending request to expire and check fresh device status."
                )
            actual = self.data.get(key)
            if key == "temperatureUnit":
                actual = {0: "00", 1: "01"}.get(actual)
            if actual == wanted and pending is None and not self.status_stale:
                return
            if starting and self.restart_delay_remaining:
                raise ServiceValidationError("Compressor restart protection is active; wait three minutes")
            issued = time.monotonic()
            deadline = issued + PENDING_SECONDS
            self.pending[key] = LocalPendingCommand(wanted, deadline, issued)
            self.last_command = feedback = LocalCommandFeedback(
                method.removeprefix("async_"), "awaiting_feedback", datetime.now(UTC), deadline, key, wanted
            )
            if stopping:
                self._last_off = issued
            self.async_update_listeners()
            try:
                async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
                    status = await getattr(self.client, method)(*args)
                actual = {
                    0x21: status.power,
                    0x23: status.target_humidity,
                    0x24: status.temperature_unit,
                }[opcode]
                if status.event_opcode != opcode or actual != args[0] or status.received_monotonic < issued:
                    raise CommandUncertainError("unexpected_report")
            except (CommandUncertainError, TimeoutError) as err:
                feedback.status = "delivery_uncertain"
                raise HomeAssistantError("Local command delivery is uncertain; it was not retried") from err
            except LocalClientError as err:
                feedback.status = "rejected"
                self.pending.pop(key, None)
                raise HomeAssistantError("Local command was not sent") from err
            else:
                self.pending.pop(key, None)
                feedback.status = "device_reported"
                feedback.acknowledged = True
                feedback.reported_at = status.received_at.astimezone(UTC)
                if stopping:
                    self._last_off = time.monotonic()
                latest = self.client.last_status
                if latest is not None:
                    # A later report can share the ACK's TCP read sequence/time.
                    # Keep command confirmation separate from the current state.
                    if (
                        starting
                        and latest.session_id == status.session_id
                        and latest.receive_sequence >= status.receive_sequence
                        and latest.received_monotonic >= status.received_monotonic
                        and latest.power is False
                    ):
                        self._last_off = time.monotonic()
                    self._on_status(latest)
            finally:
                self.async_update_listeners()

    async def async_prepare_unload(self) -> bool:
        """Finish bounded submitted commands and close before entry replacement."""
        self._closing = True
        if self._close_task is None:
            self._close_task = self.config_entry.async_create_task(
                self.hass, self._finish_unload(), f"{DOMAIN} local unload"
            )
        while True:
            try:
                await asyncio.shield(self._close_task)
                return True
            except asyncio.CancelledError:
                if self._close_task.cancelled():
                    raise
                if caller := asyncio.current_task():
                    caller.uncancel()

    async def _finish_unload(self) -> None:
        active = tuple(self._command_tasks)
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            await asyncio.gather(self._watchdog_task, return_exceptions=True)
        await self.client.async_close()
        self.async_set_update_error(UpdateFailed("Local device listener stopped"))
