"""Synthetic framing, integrity, identity, and observed-field tests."""

import unittest

from custom_components.alorair_lite.local_protocol import (
    Frame,
    FrameStream,
    keepalive_response,
    observed_status,
    power_command,
    temperature_command,
)

MAC = bytes.fromhex("020000000001")


class ProtocolTests(unittest.TestCase):
    """Synthetic protocol cases contain only the locally administered test MAC."""

    def test_power_builder_accepts_only_explicit_booleans(self):
        for value in (0, 1, None, "on", b"\x01"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                power_command(MAC, value, 1234)
        for value in (False, True):
            with self.subTest(value=value):
                decoded = Frame.decode(power_command(MAC, value, 1234), MAC)
                self.assertEqual(
                    (decoded.function, decoded.opcode, decoded.data),
                    (9, 0x21, bytes([int(value)])),
                )

    def test_only_observed_power_codes_are_boolean(self):
        for value, expected in (
            (0, False),
            (1, True),
            (2, None),
            (0x80, None),
            (0xFF, None),
        ):
            data = bytearray(34)
            data[3] = value
            data[32] = 1
            with self.subTest(value=value):
                result = observed_status(Frame(MAC, 0, 7, 0x21, bytes(data)))
                self.assertIs(result["power"], expected)
                self.assertEqual(result["event_opcode"], 0x21)
                self.assertEqual(result["temperature_display"], "fahrenheit")
                self.assertNotIn("compressor", result)
                self.assertEqual(set(result), {"power", "event_opcode", "temperature_display"})

    def test_data_length_includes_only_data(self):
        raw = temperature_command(MAC, True, 1234)
        self.assertEqual(len(raw), 30)
        self.assertEqual(raw[18:26], bytes.fromhex("0001092400000000"))
        self.assertEqual(Frame.decode(raw).data, b"\x01")

    def test_checksum_carry_changes_high_byte(self):
        low = Frame(MAC, 0, 9, 0x24, b"\x00").encode()
        high = Frame(MAC, 0, 9, 0x24, b"\xff").encode()
        self.assertEqual(int.from_bytes(high[-3:-1], "big") - int.from_bytes(low[-3:-1], "big"), 255)
        self.assertNotEqual(low[-3], high[-3])
        self.assertEqual(Frame.decode(high).data, b"\xff")

    def test_both_checksum_bytes_are_checked(self):
        for offset in (-3, -2):
            raw = bytearray(keepalive_response(MAC, 2000))
            raw[offset] ^= 1
            with self.assertRaises(ValueError):
                Frame.decode(bytes(raw))

    def test_fragmented_and_coalesced_data_with_embedded_delimiters(self):
        first = Frame(MAC, 0, 7, 0x1C, b"\x16\x0d\x0e" * 8).encode()
        second = temperature_command(MAC, False, 50)
        parser = FrameStream(MAC)
        result = []
        combined = first + second
        for offset in range(0, len(combined), 3):
            result.extend(parser.feed(combined[offset : offset + 3]))
        parser.finish()
        self.assertEqual([raw for raw, _ in result], [first, second])

    def test_wrong_identity_rejected(self):
        with self.assertRaises(ValueError):
            Frame.decode(keepalive_response(MAC, 0), bytes(6))

    def test_length_and_terminator_checked(self):
        for offset in (18, 19, -1):
            raw = bytearray(keepalive_response(MAC, 0))
            raw[offset] ^= 1
            with self.assertRaises(ValueError):
                Frame.decode(bytes(raw))

    def test_partial_stream_rejected_at_end(self):
        parser = FrameStream(MAC)
        parser.feed(keepalive_response(MAC, 0)[:-1])
        with self.assertRaises(ValueError):
            parser.finish()

    def test_unbounded_frame_rejected(self):
        raw = bytearray(keepalive_response(MAC, 0))
        raw[18:20] = b"\xff\xff"
        with self.assertRaises(ValueError):
            FrameStream(MAC).feed(bytes(raw))
