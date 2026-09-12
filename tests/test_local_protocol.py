"""Synthetic framing, integrity, identity, and observed-field tests."""

import unittest

from custom_components.alorair_lite.local_protocol import (
    Frame,
    FrameStream,
    humidity_command,
    keepalive_response,
    observed_status,
    power_command,
    temperature_command,
)

MAC = bytes.fromhex("020000000001")
# Captured 34-byte status payloads; these carry measurement bytes only, no device identity.
OFF_PAYLOAD = "200000000000000014443e12404f003f00090046000a101403940000036a00000100"
ON_PAYLOAD = "20000001000001011444371c52200038000800380008033703950000036a00000100"
CELSIUS_PAYLOAD = "200000000000000014444314444d0046000a004d000b121403940000036a00000000"
CAPTURED_STATUS = (
    (
        0x1C,
        OFF_PAYLOAD,
        {
            "temperature_display": "fahrenheit",
            "power": False,
            "target_humidity": 20,
            "inlet_celsius": 20,
            "inlet_humidity": 62,
            "outlet_celsius": 18,
            "outlet_humidity": 79,
            "inlet_grlb": 63,
            "inlet_gkg": 9,
            "outlet_grlb": 70,
            "outlet_gkg": 10,
        },
    ),
    (
        0x1C,
        ON_PAYLOAD,
        {
            "temperature_display": "fahrenheit",
            "power": True,
            "target_humidity": 55,
            "inlet_celsius": 20,
            "inlet_humidity": 55,
            "outlet_celsius": 28,
            "outlet_humidity": 32,
            "inlet_grlb": 56,
            "inlet_gkg": 8,
            "outlet_grlb": 56,
            "outlet_gkg": 8,
        },
    ),
    (
        0x24,
        CELSIUS_PAYLOAD,
        {
            "temperature_display": "celsius",
            "power": False,
            "target_humidity": 20,
            "inlet_celsius": 20,
            "inlet_humidity": 67,
            "outlet_celsius": 20,
            "outlet_humidity": 77,
            "inlet_grlb": 70,
            "inlet_gkg": 10,
            "outlet_grlb": 77,
            "outlet_gkg": 11,
        },
    ),
)


def captured(*changes, payload=OFF_PAYLOAD, opcode=0x1C):
    """Decode a captured payload after replacing individual data offsets."""
    data = bytearray.fromhex(payload)
    for offset, value in changes:
        data[offset] = value
    return observed_status(Frame(MAC, 0, 7, opcode, bytes(data)))


class ProtocolTests(unittest.TestCase):
    """Synthetic protocol cases contain only the locally administered test MAC."""

    def test_humidity_builder_uses_one_binary_byte(self):
        for target, encoded in ((20, b"\x14"), (50, b"\x32"), (55, b"\x37"), (80, b"\x50")):
            with self.subTest(target=target):
                raw = humidity_command(MAC, target, 1234)
                self.assertEqual(len(raw), 30)
                self.assertEqual(raw[18:26], bytes.fromhex("0001092300000000"))
                decoded = Frame.decode(raw, MAC)
                self.assertEqual((decoded.function, decoded.opcode, decoded.data), (9, 0x23, encoded))
        for value in (True, False, 0, 19, 21, 24, 26, 81, 255, 50.0, "50", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                humidity_command(MAC, value, 1234)

    def test_target_decodes_only_supported_values_at_offset_23(self):
        supported = {20, *range(25, 81, 5)}
        for target in range(256):
            data = bytearray(34)
            data[22], data[23], data[24], data[32] = 55, target, 50, 1
            result = observed_status(Frame(MAC, 0, 7, 0x23, bytes(data)))
            with self.subTest(target=target):
                self.assertEqual(result["target_humidity"], target if target in supported else None)
                self.assertEqual((result["inlet_humidity"], result["outlet_humidity"]), (0, 0))
                self.assertEqual((result["inlet_celsius"], result["outlet_celsius"]), (None, None))
                self.assertEqual(result["inlet_gkg"], 0)
                self.assertNotIn("fault", result)

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
                self.assertEqual(
                    set(result),
                    {
                        "power",
                        "event_opcode",
                        "temperature_display",
                        "target_humidity",
                        "inlet_celsius",
                        "inlet_humidity",
                        "outlet_celsius",
                        "outlet_humidity",
                        "inlet_grlb",
                        "inlet_gkg",
                        "outlet_grlb",
                        "outlet_gkg",
                    },
                )

    def test_captured_status_payloads_decode_measured_inlet_and_outlet_values(self):
        for opcode, payload, expected in CAPTURED_STATUS:
            with self.subTest(payload=payload):
                result = observed_status(Frame(MAC, 0, 7, opcode, bytes.fromhex(payload)))
                self.assertEqual(result, {**expected, "event_opcode": opcode})

    def test_unpaired_fahrenheit_byte_blanks_only_its_own_celsius_value(self):
        for offset, blanked, kept, humidity, measured in (
            (9, "inlet_celsius", "outlet_celsius", "inlet_humidity", 62),
            (12, "outlet_celsius", "inlet_celsius", "outlet_humidity", 79),
        ):
            with self.subTest(offset=offset):
                result = captured((offset, 0))
                self.assertIsNone(result[blanked])
                self.assertIsNotNone(result[kept])
                self.assertEqual(result[humidity], measured)

    def test_celsius_bytes_are_signed_for_sub_zero_placements(self):
        result = captured((8, 0xFB), (9, 23))
        self.assertEqual(result["inlet_celsius"], -5)
        self.assertEqual(result["inlet_humidity"], 62)
        self.assertEqual(result["outlet_celsius"], 18)

    def test_impossible_humidity_is_unknown_without_hiding_its_temperature(self):
        for offset, humidity, temperature, celsius in (
            (10, "inlet_humidity", "inlet_celsius", 20),
            (13, "outlet_humidity", "outlet_celsius", 18),
        ):
            for value in (101, 200, 255):
                with self.subTest(offset=offset, value=value):
                    result = captured((offset, value))
                    self.assertIsNone(result[humidity])
                    self.assertEqual(result[temperature], celsius)

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
