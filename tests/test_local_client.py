"""Synthetic loopback tests; no appliance, router, cloud, or captured identity."""

import asyncio
import contextlib
import dataclasses
import time
import unittest
from datetime import UTC
from unittest.mock import patch

from custom_components.alorair_lite import local_client
from custom_components.alorair_lite.local_client import (
    CommandUncertainError,
    Config,
    LocalClient,
    NotConnectedError,
    StaleStatusError,
)
from custom_components.alorair_lite.local_protocol import Frame, FrameStream

MAC = bytes.fromhex("020000000001")


def status_frame(power=0, fahrenheit=1, opcode=0x01, target=50):
    data = bytearray(34)
    data[3] = power
    data[23] = target
    data[32] = fahrenheit
    return Frame(MAC, 0, 7, opcode, bytes(data)).encode()


async def eventually(predicate):
    async with asyncio.timeout(1):
        while not predicate():  # noqa: ASYNC110 - Observe transport state without adding production test hooks.
            await asyncio.sleep(0.001)


class Device:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.parser = FrameStream(MAC)
        self.frames = asyncio.Queue()
        self.received = []
        self.task = asyncio.create_task(self._read())

    async def _read(self):
        while True:
            data = await self.reader.read(2048)
            if not data:
                return
            for _, frame in self.parser.feed(data):
                self.received.append((time.monotonic(), frame))
                self.frames.put_nowait(frame)

    async def send(self, raw):
        self.writer.write(raw)
        await self.writer.drain()

    async def next_frame(self):
        return await asyncio.wait_for(self.frames.get(), 1)

    async def close(self):
        self.writer.close()
        with contextlib.suppress(OSError):
            await self.writer.wait_closed()
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class LocalClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pacing = patch.object(local_client, "COMMAND_SPACING", 0.01)
        self.pacing.start()
        self.clients = []
        self.devices = []
        self.statuses = []
        self.disconnects = []
        self.loop_errors = []
        loop = asyncio.get_running_loop()
        self.old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: self.loop_errors.append(context))

    async def asyncTearDown(self):
        for client in self.clients:
            await client.async_close()
        for device in self.devices:
            await device.close()
        self.pacing.stop()
        asyncio.get_running_loop().set_exception_handler(self.old_handler)
        self.assertEqual(self.loop_errors, [])

    async def create_client(self, **options):
        on_status = options.pop("on_status", self.statuses.append)
        on_disconnect = options.pop(
            "on_disconnect",
            lambda session_id, reason: self.disconnects.append((session_id, reason)),
        )
        config = Config(
            "127.0.0.1",
            0,
            options.pop("allowed_client", "127.0.0.1"),
            MAC.hex(),
            command_timeout=options.pop("command_timeout", 0.12),
            **options,
        )
        client = LocalClient(config, on_status, on_disconnect)
        self.clients.append(client)
        await client.async_start()
        return client

    async def connect(self, client, initialize=True, power=0):
        reader, writer = await asyncio.open_connection(*client.address)
        device = Device(reader, writer)
        self.devices.append(device)
        if initialize:
            await device.send(status_frame(power=power))
            await eventually(lambda: client.last_status is not None)
        return device

    async def test_heartbeat_status_and_unknown_power(self):
        client = await self.create_client()
        device = await self.connect(client, False)
        self.assertFalse(client.connected)
        await device.send(Frame(MAC, 0, 9, 1, b"").encode())
        first = await device.next_frame()
        self.assertEqual((first.function, first.opcode, first.data), (9, 1, b"\x00"))
        self.assertTrue(client.connected)
        with self.assertRaises(StaleStatusError):
            await client.async_set_power(True)
        await device.send(status_frame(power=2))
        await eventually(lambda: len(self.statuses) == 1)
        status = client.last_status
        self.assertIsNone(status.power)
        self.assertEqual(status.temperature_unit, "fahrenheit")
        self.assertEqual(status.received_at.tzinfo, UTC)
        self.assertLessEqual(status.received_monotonic, time.monotonic())
        self.assertFalse(hasattr(status, "fault"))
        await device.send(Frame(MAC, 0, 9, 1, b"").encode())
        second = await device.next_frame()
        self.assertGreater(second.timestamp, first.timestamp)

    async def test_power_waits_for_matching_later_opcode(self):
        client = await self.create_client()
        device = await self.connect(client)
        command = asyncio.create_task(client.async_set_power(True))
        frame = await device.next_frame()
        self.assertEqual((frame.opcode, frame.data), (0x21, b"\x01"))
        self.assertFalse(client.last_status.power)
        await device.send(status_frame(1, opcode=0x24))
        await eventually(lambda: client.last_status.event_opcode == 0x24)
        self.assertFalse(command.done())
        await device.send(status_frame(1, opcode=0x21))
        result = await command
        self.assertTrue(result.power)
        self.assertEqual(result.event_opcode, 0x21)

    async def test_partial_precommand_status_cannot_confirm(self):
        client = await self.create_client()
        device = await self.connect(client)
        raw = status_frame(1, opcode=0x21)
        await device.send(raw[:30])
        await eventually(lambda: client._session.received_bytes == len(status_frame()) + 30)
        command = asyncio.create_task(client.async_set_power(True))
        await device.next_frame()
        await device.send(raw[30:])
        await eventually(lambda: client.last_status.power is True)
        self.assertFalse(command.done())
        await device.send(raw)
        self.assertTrue((await command).power)

    async def test_complete_prebuffered_report_cannot_confirm(self):
        client = await self.create_client(command_timeout=0.06)
        device = await self.connect(client, False)
        await device.send(status_frame(1))
        await eventually(lambda: client.last_status is not None)
        reader_paused = asyncio.Event()
        resume_reader = asyncio.Event()
        original_send = client._send

        async def pause_after_heartbeat(session, opcode, data, pending=None):
            await original_send(session, opcode, data, pending)
            if opcode == 1:
                reader_paused.set()
                await resume_reader.wait()

        with patch.object(client, "_send", pause_after_heartbeat):
            await device.send(Frame(MAC, 0, 9, 1, b"").encode())
            self.assertEqual((await device.next_frame()).opcode, 1)
            await asyncio.wait_for(reader_paused.wait(), 0.3)
            session = client._session
            consumed = session.received_bytes
            old_off = status_frame(0, opcode=0x21)
            await device.send(old_off)
            await eventually(lambda: session.reader.total_received == consumed + len(old_off))
            self.assertEqual(session.received_bytes, consumed)
            command = asyncio.create_task(client.async_set_power(False))
            frame = await device.next_frame()
            self.assertEqual((frame.opcode, frame.data), (0x21, b"\x00"))
            resume_reader.set()
            await eventually(lambda: client.last_status.power is False)
            self.assertFalse(command.done())
            # The device sends no report after the OFF command.
            with self.assertRaises(CommandUncertainError) as caught:
                await command
            self.assertEqual(caught.exception.byte_boundary, consumed + len(old_off))

    async def test_same_state_off_always_sends_and_requires_post_send_report(self):
        client = await self.create_client()
        device = await self.connect(client)
        await device.send(Frame(MAC, 0, 9, 1, b"").encode())
        await device.next_frame()
        with patch.object(local_client, "COMMAND_SPACING", 0.08):
            command = asyncio.create_task(client.async_set_power(False))
            await device.send(status_frame(0, opcode=0x21))
            await asyncio.sleep(0.015)
            self.assertFalse(command.done())
            frame = await device.next_frame()
            self.assertEqual((frame.opcode, frame.data), (0x21, b"\x00"))
            self.assertFalse(command.done())
            await device.send(status_frame(0, opcode=0x21))
            self.assertFalse((await command).power)

    async def test_stale_status_rejects_on_but_allows_verified_safety_off(self):
        client = await self.create_client(status_max_age=0.01)
        device = await self.connect(client)
        await asyncio.sleep(0.02)
        with self.assertRaises(StaleStatusError):
            await client.async_set_power(True)
        with self.assertRaises(StaleStatusError):
            await client.async_set_temperature_unit("celsius")
        command = asyncio.create_task(client.async_set_power(False))
        self.assertEqual((await device.next_frame()).data, b"\x00")
        await device.send(status_frame(0, opcode=0x21))
        self.assertFalse((await command).power)

    async def test_serialization_spacing_and_display_ack(self):
        client = await self.create_client()
        device = await self.connect(client)
        first = asyncio.create_task(client.async_set_temperature_unit("celsius"))
        self.assertEqual((await device.next_frame()).data, b"\x00")
        second = asyncio.create_task(client.async_set_power(True))
        await asyncio.sleep(0.003)
        self.assertEqual(len(device.received), 1)
        await device.send(status_frame(0, 0, 0x24))
        self.assertEqual((await first).temperature_unit, "celsius")
        second_frame = await device.next_frame()
        self.assertEqual(second_frame.opcode, 0x21)
        self.assertGreaterEqual(device.received[1][0] - device.received[0][0], 0.009)
        self.assertGreater(second_frame.timestamp, device.received[0][1].timestamp)
        await device.send(status_frame(1, 0, 0x21))
        self.assertTrue((await second).power)

    async def test_deadline_is_uncertain_with_send_boundary(self):
        client = await self.create_client(command_timeout=0.03)
        device = await self.connect(client)
        initial = client.last_status
        command = asyncio.create_task(client.async_set_power(True))
        await device.next_frame()
        with self.assertRaises(CommandUncertainError) as caught:
            await command
        error = caught.exception
        self.assertTrue(error.queued)
        self.assertEqual(error.session_id, initial.session_id)
        self.assertGreaterEqual(error.sent_monotonic, initial.received_monotonic)
        self.assertEqual(error.receive_sequence, initial.receive_sequence)
        self.assertEqual(error.byte_boundary, len(status_frame()))
        self.assertFalse(client.last_status.power)
        self.assertEqual(len(device.received), 1)

    async def test_disconnect_uncertain_and_reconnect_never_replays(self):
        client = await self.create_client()
        device = await self.connect(client)
        session_id = client.last_status.session_id
        command = asyncio.create_task(client.async_set_power(True))
        await device.next_frame()
        await device.close()
        with self.assertRaises(CommandUncertainError):
            await command
        await eventually(lambda: bool(self.disconnects))
        self.assertEqual(self.disconnects, [(session_id, "eof")])
        self.assertIsNone(client.last_status)
        new_device = await self.connect(client)
        self.assertGreater(client.last_status.session_id, session_id)
        await asyncio.sleep(0.015)
        self.assertEqual(new_device.received, [])

    async def test_cancelling_paced_on_drains_before_safety_off(self):
        client = await self.create_client()
        device = await self.connect(client)
        await device.send(Frame(MAC, 0, 9, 1, b"").encode())
        await device.next_frame()
        with patch.object(local_client, "COMMAND_SPACING", 0.08):
            on = asyncio.create_task(client.async_set_power(True))
            await asyncio.sleep(0.01)
            on.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await on
            self.assertFalse(client._command_tasks)
            off = asyncio.create_task(client.async_set_power(False))
            frame = await device.next_frame()
            self.assertEqual((frame.opcode, frame.data), (0x21, b"\x00"))
            await device.send(status_frame(0, opcode=0x21))
            await off
            await asyncio.sleep(0.09)
        self.assertEqual(
            [(frame.opcode, frame.data) for _, frame in device.received],
            [(1, b"\x00"), (0x21, b"\x00")],
        )

    async def test_cancelling_queued_command_cannot_send_after_active_command(self):
        client = await self.create_client()
        device = await self.connect(client)
        first = asyncio.create_task(client.async_set_temperature_unit("celsius"))
        await device.next_frame()
        queued_on = asyncio.create_task(client.async_set_power(True))
        await asyncio.sleep(0.005)
        queued_on.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued_on
        await device.send(status_frame(0, 0, 0x24))
        await first
        await asyncio.sleep(0.02)
        self.assertEqual(len(device.received), 1)

    async def test_close_drains_active_command_and_releases_port(self):
        client = await self.create_client()
        device = await self.connect(client)
        command = asyncio.create_task(client.async_set_power(True))
        await device.next_frame()
        await asyncio.wait_for(client.async_close(), 0.5)
        with self.assertRaises(CommandUncertainError):
            await command
        self.assertFalse(client.connected)
        self.assertFalse(client._command_tasks)
        self.assertFalse(client._connection_tasks)
        self.assertFalse(client._writers)
        self.assertEqual(self.disconnects[0][1], "client_closed")
        server = await asyncio.start_server(lambda reader, writer: writer.close(), *client.address)
        server.close()
        await server.wait_closed()
        self.assertEqual(len(device.received), 1)

    async def test_rejects_source_mac_and_concurrent_session(self):
        denied = await self.create_client(allowed_client="127.0.0.2")
        device = await self.connect(denied, False)
        await asyncio.wait_for(device.task, 0.5)
        self.assertFalse(denied.connected)
        with self.assertRaises(NotConnectedError):
            await denied.async_set_power(False)
        client = await self.create_client()
        wrong = await self.connect(client, False)
        await wrong.send(Frame(bytes.fromhex("020000000002"), 0, 9, 1, b"").encode())
        await asyncio.wait_for(wrong.task, 0.5)
        await eventually(lambda: client._session is None)
        self.assertEqual(self.disconnects[-1][1], "invalid_frame")
        first = await self.connect(client)
        second = await self.connect(client, False)
        await asyncio.wait_for(second.task, 0.5)
        self.assertTrue(client.connected)
        await first.send(Frame(MAC, 0, 9, 1, b"").encode())
        self.assertEqual((await first.next_frame()).opcode, 1)

    async def test_idle_and_partial_frame_close_allow_reconnect(self):
        client = await self.create_client(read_timeout=0.03)
        first = await self.connect(client)
        await asyncio.wait_for(first.task, 0.3)
        await eventually(lambda: len(self.disconnects) == 1)
        self.assertEqual(self.disconnects[0][1], "idle_timeout")
        second = await self.connect(client, False)
        await second.send(status_frame()[:25])
        second.writer.write_eof()
        await eventually(lambda: len(self.disconnects) == 2)
        self.assertEqual(self.disconnects[1][1], "invalid_frame")

    async def test_async_callback_can_await_command_without_reader_deadlock(self):
        complete = asyncio.Event()

        async def callback(status):
            if status.event_opcode == 1:
                result = await client.async_set_temperature_unit("celsius")
                self.assertEqual(result.temperature_unit, "celsius")
                complete.set()

        client = await self.create_client(on_status=callback)
        device = await self.connect(client)
        self.assertEqual((await device.next_frame()).opcode, 0x24)
        await device.send(status_frame(0, 0, 0x24))
        await asyncio.wait_for(complete.wait(), 0.3)

    async def test_callback_error_uses_fixed_label_and_closes_connection(self):
        errors = []
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: errors.append(context))

        def callback(status):
            raise RuntimeError("private fixture must not appear")

        try:
            client = await self.create_client(on_status=callback)
            device = await self.connect(client, False)
            await device.send(status_frame())
            await eventually(lambda: bool(self.disconnects))
            self.assertEqual(self.disconnects[0][1], "callback_error")
            self.assertEqual(errors, [{"message": "Local client callback failed"}])
        finally:
            loop.set_exception_handler(old_handler)

    async def test_humidity_requires_matching_later_opcode_and_value(self):
        client = await self.create_client()
        device = await self.connect(client, power=1)
        command = asyncio.create_task(client.async_set_humidity(55))
        frame = await device.next_frame()
        self.assertEqual((frame.opcode, frame.data), (0x23, b"\x37"))
        self.assertEqual(client.last_status.target_humidity, 50)
        await device.send(status_frame(opcode=0x24, target=55))
        await eventually(lambda: client.last_status.target_humidity == 55)
        self.assertFalse(command.done())
        await device.send(status_frame(opcode=0x23, target=0))
        await eventually(lambda: client.last_status.target_humidity is None)
        self.assertFalse(command.done())
        await device.send(status_frame(opcode=0x23, target=55))
        result = await command
        self.assertEqual(result.target_humidity, 55)
        self.assertEqual(result.event_opcode, 0x23)
        legacy = local_client.DeviceStatus(
            result.session_id,
            result.receive_sequence,
            result.received_at,
            result.received_monotonic,
            result.power,
            result.temperature_unit,
            result.event_opcode,
        )
        self.assertIsNone(legacy.target_humidity)

    async def test_humidity_rejects_reported_off_or_unknown(self):
        for power in (0, 2):
            with self.subTest(power=power):
                client = await self.create_client()
                device = await self.connect(client, power=power)
                with self.assertRaises(local_client.CommandNotSentError):
                    await client.async_set_humidity(55)
                self.assertEqual(device.received, [])
                self.assertIsNone(client._session.pending)

    async def test_off_during_humidity_pacing_prevents_write_and_allows_restoration(self):
        client = await self.create_client()
        device = await self.connect(client, power=1)
        await device.send(Frame(MAC, 0, 9, 1, b"").encode())
        await device.next_frame()
        with patch.object(local_client, "COMMAND_SPACING", 0.08):
            command = asyncio.create_task(client.async_set_humidity(55))
            await eventually(lambda: client._session.pending is not None)
            self.assertFalse(client._session.pending.queued)
            await device.send(status_frame(power=0))
            await eventually(lambda: client.last_status.power is False)
            with self.assertRaises(local_client.CommandNotSentError):
                await command
            await asyncio.sleep(0.09)
            self.assertEqual([(frame.opcode, frame.data) for _, frame in device.received], [(1, b"\x00")])
            restore = asyncio.create_task(client.async_set_humidity(20, allow_stale=True))
            frame = await device.next_frame()
            self.assertEqual((frame.opcode, frame.data), (0x23, b"\x14"))
            await device.send(status_frame(power=0, opcode=0x23, target=20))
            self.assertEqual((await restore).target_humidity, 20)

    async def test_humidity_stale_restore_is_explicit_and_requires_verified_session(self):
        client = await self.create_client(status_max_age=0.01)
        with self.assertRaises(NotConnectedError):
            await client.async_set_humidity(20, allow_stale=True)
        device = await self.connect(client)
        for invalid in (True, False, 21, 26, 81, 50.0, "50"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                await client.async_set_humidity(invalid, allow_stale=True)
        with self.assertRaises(ValueError):
            await client.async_set_humidity(20, allow_stale="yes")
        await asyncio.sleep(0.02)
        with self.assertRaises(StaleStatusError):
            await client.async_set_humidity(20)
        command = asyncio.create_task(client.async_set_humidity(20, allow_stale=True))
        frame = await device.next_frame()
        self.assertEqual((frame.opcode, frame.data), (0x23, b"\x14"))
        self.assertFalse(command.done())
        await device.send(status_frame(opcode=0x23, target=20))
        self.assertEqual((await command).target_humidity, 20)
        self.assertEqual(len(device.received), 1)

    async def test_buffered_humidity_report_cannot_confirm_a_later_write(self):
        client = await self.create_client(command_timeout=0.06)
        device = await self.connect(client, power=1)
        paused = asyncio.Event()
        resume = asyncio.Event()
        original_send = client._send

        async def pause_after_heartbeat(session, opcode, data, pending=None):
            await original_send(session, opcode, data, pending)
            if opcode == 1:
                paused.set()
                await resume.wait()

        with patch.object(client, "_send", pause_after_heartbeat):
            await device.send(Frame(MAC, 0, 9, 1, b"").encode())
            await device.next_frame()
            await asyncio.wait_for(paused.wait(), 0.3)
            session = client._session
            consumed = session.received_bytes
            old_report = status_frame(opcode=0x23, target=55)
            await device.send(old_report)
            await eventually(lambda: session.reader.total_received == consumed + len(old_report))
            command = asyncio.create_task(client.async_set_humidity(55))
            self.assertEqual((await device.next_frame()).data, b"\x37")
            resume.set()
            await eventually(lambda: client.last_status.target_humidity == 55)
            with self.assertRaises(CommandUncertainError):
                await command
            self.assertEqual(len(device.received), 2)

    async def test_cancelled_humidity_change_cannot_follow_restoration(self):
        client = await self.create_client()
        device = await self.connect(client, power=1)
        await device.send(Frame(MAC, 0, 9, 1, b"").encode())
        await device.next_frame()
        with patch.object(local_client, "COMMAND_SPACING", 0.08):
            change = asyncio.create_task(client.async_set_humidity(55))
            await asyncio.sleep(0.01)
            change.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await change
            restore = asyncio.create_task(client.async_set_humidity(20, allow_stale=True))
            frame = await device.next_frame()
            self.assertEqual((frame.opcode, frame.data), (0x23, b"\x14"))
            await device.send(status_frame(opcode=0x23, target=20))
            await restore
            await asyncio.sleep(0.09)
        self.assertEqual([(frame.opcode, frame.data) for _, frame in device.received], [(1, b"\x00"), (0x23, b"\x14")])

    async def test_validation_before_any_command(self):
        config = Config("127.0.0.1", 0, "127.0.0.1", MAC.hex())
        for fields in (
            {"listen_host": "0.0.0.0"},
            {"allowed_client": "localhost"},
            {"mac": "bad"},
            {"command_timeout": float("nan")},
            {"listen_port": -1},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                dataclasses.replace(config, **fields)
        client = await self.create_client()
        with self.assertRaises(ValueError):
            await client.async_set_power(1)
        with self.assertRaises(ValueError):
            await client.async_set_temperature_unit("kelvin")
        with self.assertRaises(NotConnectedError):
            await client.async_set_power(False)


if __name__ == "__main__":
    unittest.main()
