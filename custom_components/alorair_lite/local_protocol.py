"""Observed Storm/Lite framing; no network access or unverified command opcodes."""

import math
from dataclasses import dataclass

MAGIC = b"\x0d\x0e"
MAX_DATA = 256
HUMIDITY_TARGETS = frozenset((20, *range(25, 81, 5)))


@dataclass(frozen=True)
class Frame:
    mac: bytes
    timestamp: int
    function: int
    opcode: int
    data: bytes
    reserved: bytes = bytes(4)

    def encode(self) -> bytes:
        if len(self.mac) != 6 or len(self.reserved) != 4:
            raise ValueError("Invalid identity or reserved field length")
        if len(self.data) > MAX_DATA or not 0 <= self.timestamp <= 0xFFFFFFFF:
            raise ValueError("Frame exceeds supported bounds")
        prefix = (
            MAGIC
            + bytes(6)
            + self.mac
            + self.timestamp.to_bytes(4, "big")
            + len(self.data).to_bytes(2, "big")
            + bytes([self.function, self.opcode])
            + self.reserved
            + self.data
        )
        return prefix + (sum(prefix) & 0xFFFF).to_bytes(2, "big") + b"\x16"

    @classmethod
    def decode(cls, raw: bytes, expected_mac: bytes | None = None) -> Frame:
        if len(raw) < 29 or raw[:8] != MAGIC + bytes(6):
            raise ValueError("Invalid frame header")
        data_length = int.from_bytes(raw[18:20], "big")
        if data_length > MAX_DATA or len(raw) != 29 + data_length:
            raise ValueError("Invalid declared frame length")
        if raw[-1] != 0x16 or (sum(raw[:-3]) & 0xFFFF) != int.from_bytes(raw[-3:-1], "big"):
            raise ValueError("Invalid two-byte checksum or terminator")
        if expected_mac is not None and raw[8:14] != expected_mac:
            raise ValueError("Unexpected appliance identity")
        return cls(
            raw[8:14],
            int.from_bytes(raw[14:18], "big"),
            raw[20],
            raw[21],
            raw[26:-3],
            raw[22:26],
        )


class FrameStream:
    """Split a bounded byte stream by the observed length field."""

    def __init__(self, expected_mac: bytes | None = None) -> None:
        self.buffer = bytearray()
        self.expected_mac = expected_mac

    def feed(self, data: bytes) -> list[tuple[bytes, Frame]]:
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 20:
            if self.buffer[:8] != MAGIC + bytes(6):
                raise ValueError("Unexpected stream header")
            size = 29 + int.from_bytes(self.buffer[18:20], "big")
            if size > MAX_DATA + 29:
                raise ValueError("Declared stream frame too large")
            if len(self.buffer) < size:
                break
            raw = bytes(self.buffer[:size])
            frames.append((raw, Frame.decode(raw, self.expected_mac)))
            del self.buffer[:size]
        if len(self.buffer) > MAX_DATA + 29:
            raise ValueError("Unbounded partial frame")
        return frames

    def finish(self) -> None:
        if self.buffer:
            raise ValueError("Partial frame at stream end")


def keepalive_response(mac: bytes, timestamp: int) -> bytes:
    return Frame(mac, timestamp, 0x09, 0x01, b"\x00").encode()


def temperature_command(mac: bytes, fahrenheit: bool, timestamp: int) -> bytes:
    if type(fahrenheit) is not bool:
        raise ValueError("Display selection must be boolean")
    return Frame(mac, timestamp, 0x09, 0x24, bytes([int(fahrenheit)])).encode()


def power_command(mac: bytes, on: bool, timestamp: int) -> bytes:
    """Build the captured enabled-state command, not a compressor measurement."""
    if type(on) is not bool:
        raise ValueError("Power selection must be boolean")
    return Frame(mac, timestamp, 0x09, 0x21, bytes([int(on)])).encode()


def humidity_target(value: object) -> int | None:
    """Accept supported targets; 20 selects continuous operation."""
    return value if type(value) is int and value in HUMIDITY_TARGETS else None


def humidity_command(mac: bytes, target: int, timestamp: int) -> bytes:
    """Encode the target as one binary byte, not decimal text or packed digits."""
    if humidity_target(target) is None:
        raise ValueError("Humidity target must be 20 or 25–80 in steps of five")
    return Frame(mac, timestamp, 0x09, 0x23, bytes([target])).encode()


def _temperature(data: bytes, index: int) -> int | None:
    """Report signed Celsius only when the paired Fahrenheit byte confirms this layout."""
    celsius = int.from_bytes(data[index : index + 1], "big", signed=True)
    fahrenheit = int.from_bytes(data[index + 1 : index + 2], "big", signed=True)
    return celsius if fahrenheit == math.floor(celsius * 9 / 5 + 32) else None


def _humidity(value: int) -> int | None:
    return value if 0 <= value <= 100 else None


def observed_status(frame: Frame) -> dict[str, bool | int | str | None]:
    if frame.function != 0x07 or len(frame.data) != 34:
        raise ValueError("Not the observed Storm/Lite status layout")
    # Display and 0/1 enabled state are established by captured command echoes.
    if frame.data[32] not in (0, 1):
        raise ValueError("Unknown temperature display value")
    data = frame.data
    # Inlet/outlet block established from 398 captured frames: F byte = floor(C*9/5+32),
    # gr/lb = 7*g/kg, g/kg matches the psychrometric mixing ratio of (C, RH).
    return {
        "temperature_display": "fahrenheit" if data[32] else "celsius",
        "power": bool(data[3]) if data[3] in (0, 1) else None,
        "target_humidity": humidity_target(data[23]),
        "event_opcode": frame.opcode,
        "inlet_celsius": _temperature(data, 8),
        "inlet_humidity": _humidity(data[10]),
        "outlet_celsius": _temperature(data, 11),
        "outlet_humidity": _humidity(data[13]),
        "inlet_grlb": int.from_bytes(data[14:16], "big"),
        "inlet_gkg": int.from_bytes(data[16:18], "big"),
        "outlet_grlb": int.from_bytes(data[18:20], "big"),
        "outlet_gkg": int.from_bytes(data[20:22], "big"),
    }
