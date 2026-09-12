"""Local, inbound-only Storm/Lite transport; no Home Assistant or cloud access."""

import asyncio
import inspect
import ipaddress
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from .local_protocol import Frame, FrameStream, humidity_target, observed_status

COMMAND_SPACING = 1.1
READ_BYTES = 2048
CLOSE_TIMEOUT = 2.0
CALLBACK_TIMEOUT = 2.0
CALLBACK_QUEUE_SIZE = 64
DisconnectReason = Literal[
    "eof",
    "invalid_frame",
    "idle_timeout",
    "connection_error",
    "client_closed",
    "callback_error",
]


class LocalClientError(Exception):
    """A fixed-label local transport failure."""


class NotConnectedError(LocalClientError):
    """The selected appliance has no current verified connection."""


class StaleStatusError(LocalClientError):
    """No fresh status exists on the current connection."""


class CommandNotSentError(LocalClientError):
    """The bounded command operation ended before bytes were queued."""


class CommandUncertainError(LocalClientError):
    """Command bytes were queued but no qualifying report was received."""

    def __init__(
        self,
        reason: str,
        session_id: int | None = None,
        sent_monotonic: float | None = None,
        receive_sequence: int | None = None,
        byte_boundary: int | None = None,
    ) -> None:
        self.reason = reason
        self.queued = True
        self.session_id = session_id
        self.sent_monotonic = sent_monotonic
        self.receive_sequence = receive_sequence
        self.byte_boundary = byte_boundary
        super().__init__("Command delivery is uncertain; no automatic retry was sent")


@dataclass(frozen=True)
class Config:
    listen_host: str
    listen_port: int
    allowed_client: str
    mac: str
    command_timeout: float = 10.0
    status_max_age: float = 90.0
    read_timeout: float = 90.0
    drain_timeout: float = 2.0

    def __post_init__(self) -> None:
        for field in ("listen_host", "allowed_client"):
            address = ipaddress.IPv4Address(getattr(self, field))
            if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
                raise ValueError("A concrete unicast IPv4 address is required")
            object.__setattr__(self, field, str(address))
        if not 0 <= self.listen_port <= 65535:
            raise ValueError("Listen port is outside 0..65535")
        if len(self.mac) != 12 or any(char not in "0123456789abcdefABCDEF" for char in self.mac):
            raise ValueError("MAC must be twelve hexadecimal characters")
        object.__setattr__(self, "mac", self.mac.upper())
        for field in (
            "command_timeout",
            "status_max_age",
            "read_timeout",
            "drain_timeout",
        ):
            value = getattr(self, field)
            if not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError("Timeouts must be finite, positive, and at most 300 seconds")


@dataclass(frozen=True)
class DeviceStatus:
    session_id: int
    receive_sequence: int
    received_at: datetime
    received_monotonic: float
    power: bool | None
    temperature_unit: str
    event_opcode: int
    target_humidity: int | None = None
    inlet_celsius: int | None = None
    inlet_humidity: int | None = None
    outlet_celsius: int | None = None
    outlet_humidity: int | None = None
    inlet_gkg: int | None = None
    outlet_gkg: int | None = None
    inlet_grlb: int | None = None
    outlet_grlb: int | None = None


@dataclass
class _Pending:
    opcode: int
    expected: object
    future: asyncio.Future
    queued: bool = False
    receive_sequence: int = 0
    byte_boundary: int = 0
    sent_monotonic: float | None = None
    allow_stale: bool = False


class _CountingReader(asyncio.StreamReader):
    """Count protocol-delivered bytes, including bytes awaiting a read."""

    def __init__(self):
        super().__init__(limit=READ_BYTES)
        self.total_received = 0

    def feed_data(self, data):
        self.total_received += len(data)
        super().feed_data(data)


@dataclass
class _Session:
    session_id: int
    reader: _CountingReader
    writer: asyncio.StreamWriter
    verified: bool = False
    receive_sequence: int = 0
    received_bytes: int = 0
    pending: _Pending | None = None
    reason: DisconnectReason = "eof"


class LocalClient:
    """Receive one configured appliance; callbacks must not block the event loop."""

    def __init__(
        self,
        config: Config,
        on_status: Callable[[DeviceStatus], Awaitable[None] | None] | None = None,
        on_disconnect: Callable[[int, DisconnectReason], Awaitable[None] | None] | None = None,
    ) -> None:
        self.config = config
        self._mac = bytes.fromhex(config.mac)
        self._on_status = on_status
        self._on_disconnect = on_disconnect
        self._server = None
        self._address = None
        self._session = None
        self._last_status = None
        self._session_counter = 0
        self._closing = False
        self._close_task = None
        self._connection_tasks = set()
        self._command_tasks = set()
        self._writers = set()
        self._send_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._last_outbound_time = None
        self._last_outbound_timestamp = None
        self._callbacks = asyncio.Queue(maxsize=CALLBACK_QUEUE_SIZE)
        self._callback_task = None

    @property
    def address(self) -> tuple[str, int] | None:
        return self._address

    @property
    def connected(self) -> bool:
        return bool(
            not self._closing and self._session and self._session.verified and not self._session.writer.is_closing()
        )

    @property
    def last_status(self) -> DeviceStatus | None:
        return self._last_status if self.connected else None

    async def async_start(self) -> tuple[str, int]:
        """Bind the local listener without making an outbound connection."""
        if self._server is not None or self._closing:
            raise LocalClientError("Local client cannot be started twice")
        self._server = await asyncio.get_running_loop().create_server(
            lambda: asyncio.StreamReaderProtocol(_CountingReader(), self._accept),
            self.config.listen_host,
            self.config.listen_port,
        )
        self._address = self._server.sockets[0].getsockname()[:2]
        self._callback_task = asyncio.create_task(self._dispatch_callbacks())
        return self._address

    @staticmethod
    def _track(task, collection):
        collection.add(task)

        def finished(done):
            collection.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)

    def _accept(self, reader, writer):
        self._writers.add(writer)
        peer = writer.get_extra_info("peername")
        if self._closing or not peer or peer[0] != self.config.allowed_client or self._session is not None:
            writer.close()
            self._track(asyncio.create_task(self._close_writer(writer)), self._connection_tasks)
            return
        self._session_counter += 1
        session = _Session(self._session_counter, reader, writer)
        self._session = session
        self._last_status = None
        self._track(asyncio.create_task(self._read_session(session)), self._connection_tasks)

    def _enqueue_callback(self, session_id, kind, *args):
        callback = self._on_status if kind == "status" else self._on_disconnect
        if callback is None:
            return
        try:
            self._callbacks.put_nowait((session_id, kind, callback, args))
        except asyncio.QueueFull:
            # Fail the connection rather than silently lose a state transition.
            asyncio.get_running_loop().call_exception_handler({"message": "Local client callback queue overflow"})
            self._discard_queued_statuses()
            if kind == "disconnect":
                self._callbacks.put_nowait((session_id, kind, callback, args))
            elif self._session is not None and self._session.session_id == session_id:
                self._session.reason = "callback_error"
                self._session.writer.close()

    def _discard_queued_statuses(self):
        retained = []
        while not self._callbacks.empty():
            item = self._callbacks.get_nowait()
            self._callbacks.task_done()
            if item[1] == "disconnect":
                retained.append(item)
        for item in retained[-(CALLBACK_QUEUE_SIZE - 1) :]:
            self._callbacks.put_nowait(item)

    async def _dispatch_callbacks(self):
        while True:
            session_id, kind, callback, args = await self._callbacks.get()
            try:
                result = callback(*args)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, CALLBACK_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception:
                asyncio.get_running_loop().call_exception_handler({"message": "Local client callback failed"})
                if kind == "status" and self._session is not None and self._session.session_id == session_id:
                    self._session.reason = "callback_error"
                    self._session.writer.close()
            finally:
                self._callbacks.task_done()

    async def _read_session(self, session):
        parser = FrameStream(self._mac)
        try:
            while not self._closing:
                chunk = await asyncio.wait_for(session.reader.read(READ_BYTES), self.config.read_timeout)
                if not chunk:
                    parser.finish()
                    break
                first_frame_offset = session.received_bytes - len(parser.buffer)
                session.received_bytes += len(chunk)
                session.receive_sequence += 1
                received_at = datetime.now(UTC)
                received_monotonic = time.monotonic()
                for raw, frame in parser.feed(chunk):
                    frame_offset = first_frame_offset
                    first_frame_offset += len(raw)
                    session.verified = True
                    if frame.function == 9 and frame.opcode == 1 and frame.data == b"" and frame.reserved == bytes(4):
                        await self._send(session, 1, b"\x00")
                    elif frame.function == 7 and len(frame.data) == 34:
                        decoded = observed_status(frame)
                        status = DeviceStatus(
                            session.session_id,
                            session.receive_sequence,
                            received_at,
                            received_monotonic,
                            decoded["power"],
                            decoded["temperature_display"],
                            frame.opcode,
                            decoded["target_humidity"],
                            decoded["inlet_celsius"],
                            decoded["inlet_humidity"],
                            decoded["outlet_celsius"],
                            decoded["outlet_humidity"],
                            decoded["inlet_gkg"],
                            decoded["outlet_gkg"],
                            decoded["inlet_grlb"],
                            decoded["outlet_grlb"],
                        )
                        self._last_status = status
                        pending = session.pending
                        if (
                            pending is not None
                            and pending.queued
                            and not pending.future.done()
                            and frame.opcode == pending.opcode
                            and session.receive_sequence > pending.receive_sequence
                            and frame_offset >= pending.byte_boundary
                        ):
                            actual = (
                                status.power
                                if pending.opcode == 0x21
                                else status.target_humidity
                                if pending.opcode == 0x23
                                else status.temperature_unit
                            )
                            if actual == pending.expected:
                                pending.future.set_result(status)
                        self._enqueue_callback(session.session_id, "status", status)
        except asyncio.CancelledError:
            session.reason = "client_closed" if self._closing else "connection_error"
            raise
        except TimeoutError:
            session.reason = "idle_timeout"
        except ValueError:
            session.reason = "invalid_frame"
        except OSError, LocalClientError:
            session.reason = "client_closed" if self._closing else "connection_error"
        finally:
            if self._closing:
                session.reason = "client_closed"
            if self._session is session:
                self._session = None
                self._last_status = None
            pending = session.pending
            if pending is not None and not pending.future.done():
                error = (
                    self._uncertain(session, pending, "disconnected")
                    if pending.queued
                    else NotConnectedError("Connection closed before the command was queued")
                )
                pending.future.set_exception(error)
            await self._close_writer(session.writer)
            self._enqueue_callback(session.session_id, "disconnect", session.session_id, session.reason)

    def _require_fresh_status(self, expected_session=None, allow_stale=False):
        session = self._session
        if not self.connected or (expected_session is not None and session is not expected_session):
            raise NotConnectedError("No current verified appliance connection")
        if allow_stale:
            return session
        status = self._last_status
        if (
            status is None
            or status.session_id != session.session_id
            or not 0 <= time.monotonic() - status.received_monotonic <= self.config.status_max_age
        ):
            raise StaleStatusError("Fresh status is required on the current connection")
        return session

    async def _send(self, session, opcode, data, pending=None):
        while True:
            async with self._send_lock:
                if self._closing or self._session is not session or session.writer.is_closing():
                    raise NotConnectedError("Connection closed before sending")
                if pending is not None:
                    self._require_fresh_status(session, pending.allow_stale or (opcode == 0x21 and data == b"\x00"))
                delay = 0.0
                if pending is not None and self._last_outbound_time is not None:
                    delay = max(
                        0.0,
                        COMMAND_SPACING - (time.monotonic() - self._last_outbound_time),
                    )
                if delay == 0:
                    if (
                        opcode == 0x23
                        and pending is not None
                        and not pending.allow_stale
                        and (self._last_status is None or self._last_status.power is not True)
                    ):
                        raise CommandNotSentError("Appliance must report enabled before changing humidity")
                    timestamp = int(time.time())
                    if self._last_outbound_timestamp is not None:
                        timestamp = max(timestamp, self._last_outbound_timestamp + 1)
                    raw = Frame(self._mac, timestamp, 9, opcode, data).encode()
                    if pending is not None:
                        pending.receive_sequence = session.receive_sequence
                        # Buffered frames received before this write cannot be ACKs.
                        pending.byte_boundary = session.reader.total_received
                        pending.sent_monotonic = time.monotonic()
                        pending.queued = True
                    session.writer.write(raw)
                    self._last_outbound_time = time.monotonic()
                    self._last_outbound_timestamp = timestamp
                    await asyncio.wait_for(session.writer.drain(), self.config.drain_timeout)
                    return
            await asyncio.sleep(delay)

    async def async_set_power(self, on: bool) -> DeviceStatus:
        """Send once and await a later report; verified-session OFF permits stale status."""
        if type(on) is not bool:
            raise ValueError("Power selection must be boolean")
        return await self._command(0x21, bytes([int(on)]), on)

    async def async_set_temperature_unit(self, unit: str) -> DeviceStatus:
        """Send a display selection and await its later matching report."""
        if unit not in ("celsius", "fahrenheit"):
            raise ValueError("Temperature unit must be celsius or fahrenheit")
        return await self._command(0x24, bytes([int(unit == "fahrenheit")]), unit)

    async def async_set_humidity(self, target: int, *, allow_stale: bool = False) -> DeviceStatus:
        """Require reported ON at send time, except for explicit bounded restoration."""
        if humidity_target(target) is None:
            raise ValueError("Humidity target must be 20 or 25–80 in steps of five")
        if type(allow_stale) is not bool:
            raise ValueError("Restoration freshness override must be boolean")
        return await self._command(0x23, bytes([target]), target, allow_stale=allow_stale)

    async def _command(self, opcode, data, expected, *, allow_stale=False):
        session = self._require_fresh_status(allow_stale=allow_stale or (opcode == 0x21 and expected is False))
        task = asyncio.create_task(self._execute_command(session, opcode, data, expected, allow_stale))
        self._track(task, self._command_tasks)
        try:
            await asyncio.wait({task})
            return task.result()
        except asyncio.CancelledError:
            # Drain cancellation before restoration can queue another command.
            task.cancel()
            while not task.done():
                try:
                    await asyncio.wait({task})
                except asyncio.CancelledError:
                    continue
            if not task.cancelled():
                task.exception()
            raise

    @staticmethod
    def _uncertain(session, pending, reason):
        return CommandUncertainError(
            reason,
            session.session_id,
            pending.sent_monotonic,
            pending.receive_sequence,
            pending.byte_boundary,
        )

    async def _execute_command(self, session, opcode, data, expected, allow_stale=False):
        pending = None
        acquired = False
        try:
            await asyncio.wait_for(self._command_lock.acquire(), self.config.command_timeout)
            acquired = True
            self._require_fresh_status(session, allow_stale or (opcode == 0x21 and expected is False))
            pending = _Pending(opcode, expected, asyncio.get_running_loop().create_future(), allow_stale=allow_stale)
            session.pending = pending
            await asyncio.wait_for(
                self._send(session, opcode, data, pending),
                COMMAND_SPACING + self.config.drain_timeout + 1,
            )
            return await asyncio.wait_for(asyncio.shield(pending.future), self.config.command_timeout)
        except asyncio.CancelledError:
            if pending is not None and pending.queued:
                raise self._uncertain(session, pending, "cancelled_or_closed") from None
            raise CommandNotSentError("Local client closed before the command was queued") from None
        except TimeoutError, OSError:
            if pending is not None and pending.future.done() and not pending.future.cancelled():
                return pending.future.result()
            if pending is not None and pending.queued:
                raise self._uncertain(session, pending, "deadline_or_write_failure") from None
            raise CommandNotSentError("Command deadline expired before bytes were queued") from None
        finally:
            if pending is not None:
                if session.pending is pending:
                    session.pending = None
                if not pending.future.done():
                    pending.future.cancel()
                elif not pending.future.cancelled():
                    pending.future.exception()
            if acquired:
                self._command_lock.release()

    async def _close_writer(self, writer):
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), CLOSE_TIMEOUT)
        except asyncio.CancelledError:
            writer.transport.abort()
            raise
        except TimeoutError, OSError:
            writer.transport.abort()
        finally:
            self._writers.discard(writer)

    async def async_close(self) -> None:
        """Drain transport tasks and release the listener without changing device settings."""
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._cleanup())
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._close_task)
                break
            except asyncio.CancelledError:
                cancelled = True
                if self._close_task.cancelled():
                    raise
        if cancelled:
            raise asyncio.CancelledError

    async def _cleanup(self):
        if self._server is not None:
            self._server.close()
        for writer in tuple(self._writers):
            writer.close()
        tasks = tuple(self._connection_tasks | self._command_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), CLOSE_TIMEOUT * 2)
            except TimeoutError:
                pass
        for writer in tuple(self._writers):
            writer.transport.abort()
        self._writers.clear()
        self._session = None
        self._last_status = None
        if self._server is not None:
            await asyncio.wait_for(self._server.wait_closed(), CLOSE_TIMEOUT)
        self._discard_queued_statuses()
        if self._callback_task is not None:
            try:
                await asyncio.wait_for(self._callbacks.join(), CALLBACK_TIMEOUT)
            except TimeoutError:
                pass
            self._callback_task.cancel()
            await asyncio.gather(self._callback_task, return_exceptions=True)
