"""Bounded ALORAIR-Lite cloud API with exact device identity checks."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

DEFAULT_BASE_URL = "http://online-app1.toovem.com:8081/rest/api/"
PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC2wuZQGoCioJg4RZjzTmyeGqGLrjP4
jR7AF85d7j/jhghoRZeFatM+V5WkvS6irQ2Q7v4DFW+4oLYUbDuTQAgUn+Bbo/MnSaY
LI9WssM+W6j5mrxd8ca+PRoRF+pPQzIpMAjwDdY+pPi6l/AzEiRBurguAYve8kBKe8l
ActVS+mwIDAQAB
-----END PUBLIC KEY-----
"""
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PAGES = 100
PAGE_SIZE = 10
EXPERIMENTAL_MUTATION_ACTIONS = frozenset({"rename", "location", "extend_filter", "reset_filter"})
EXPERIMENTAL_ACTIONS = EXPERIMENTAL_MUTATION_ACTIONS | {
    "history",
    "operation_time",
    "filters",
    "filter_detail",
    "firmware_check",
    "firmware_history",
}
MAX_HISTORY_SAMPLES = 4096
MAX_FILTERS = 20
_EXPERIMENTAL_GET_PATHS = frozenset(
    {"iot/device/getDeviceHistoryDateList", "iot/device/getDeviceHistoryTime", "iot/firmware/getUpgradeListByDevice/"}
)
_EXPERIMENTAL_POST_PATHS = frozenset(
    {"iot/device/updateDeviceName", "iot/device/updateDeviceLocation", "iot/device/updateFilterHour"}
)
_HISTORY_FIELDS = {
    "avgInTemp": "in_celsius",
    "avgInTempEn": "in_fahrenheit",
    "avgOutTemp": "out_celsius",
    "avgOutTempEn": "out_fahrenheit",
    "avgInHumidity": "in_humidity",
    "avgOutHumidity": "out_humidity",
    "avgInGrlb": "in_grains_per_pound",
    "avgInGkg": "in_grams_per_kilogram",
    "avgOutGrlb": "out_grains_per_pound",
    "avgOutGkg": "out_grams_per_kilogram",
}
STATUS_FIELDS = frozenset(
    {
        "powerStatus",
        "drainStatus",
        "defrostingStatus",
        "locateFunction",
        "errCode",
        "currentHumidity",
        "inHumidity",
        "outHumidity",
        "inCelsius",
        "outCelsius",
        "inFahrenheit",
        "outFahrenheit",
        "inGkg",
        "outGkg",
        "inGrlb",
        "outGrlb",
        "temperatureUnit",
        "humidityUnit",
        "pints",
        "singleWorktime",
        "cfm",
        "coilTemperature",
        "controlModel",
        "gppHumidity",
        "isHumidityUnit",
    }
)
METADATA_FIELDS = frozenset(
    {
        "name",
        "productName",
        "machineBrandName",
        "version",
        "firmwareVersion",
        "hardwareVersion",
        "model",
    }
)
_DIAGNOSTIC_REASONS = {
    "A complete device MAC address is required.": "invalid_mac_shape",
    "The vendor returned an invalid identity.": "invalid_identity_shape",
    "Credentials cannot be encoded for the vendor login.": "credentials_encoding",
    "An account username and password are required.": "credentials_missing",
    "Invalid cloud API base URL.": "invalid_base_url",
    "The vendor uses unencrypted HTTP; explicit transport consent is required.": "http_consent_required",
    "Use a session with trust_env=False to keep credentials out of environment proxies.": "environment_proxy_refused",
    "The request timeout must be finite and positive.": "invalid_timeout",
    "Unsupported cloud endpoint.": "unsupported_endpoint",
    "The vendor returned an invalid session token.": "invalid_token_shape",
    "The vendor rejected the session or credentials.": "authentication_rejected",
    "Cloud redirect refused; credentials were not forwarded.": "redirect_refused",
    "The vendor returned an unsuccessful HTTP status.": "http_status_rejected",
    "Cloud response exceeded the size limit.": "response_size_limit",
    "The vendor returned invalid JSON.": "invalid_json",
    "The cloud request could not be completed.": "transport_failure",
    "The vendor returned an unsupported response.": "response_shape",
    "The vendor rejected the request or returned an unsupported response.": "vendor_response_rejected",
    "The vendor login did not return a usable session.": "login_session_missing",
    "The vendor did not return an account identity.": "account_identity_missing",
    "The vendor returned an invalid account identity.": "account_identity_invalid",
    "The vendor returned an unsupported device list.": "device_list_shape",
    "Device pagination exceeded the supported bounds.": "pagination_bounds",
    "The vendor returned an invalid device entry.": "device_entry_shape",
    "The vendor returned conflicting device identities.": "conflicting_device_identities",
    "Device pagination did not terminate.": "pagination_nontermination",
    "The exact MAC was not found among this account's owned devices.": "owned_device_not_found",
    "Device detail did not match the requested identity.": "detail_identity_mismatch",
    "Device ownership could not be verified.": "detail_ownership_mismatch",
}
_DIAGNOSTIC_STAGES = frozenset({"login", "user_info", "device_list", "device_detail", "control", "request"})


class AlorairError(Exception):
    """Base error with a credential-safe message."""

    def __init__(self, message: str, *, stage: str = "request") -> None:
        super().__init__(message)
        self.diagnostic_reason = _DIAGNOSTIC_REASONS.get(message, "unspecified_error")
        self.diagnostic_stage = stage if stage in _DIAGNOSTIC_STAGES else "request"
        self.vendor_code: int | None = None


class AuthenticationError(AlorairError):
    """The vendor rejected authentication."""


class CannotConnect(AlorairError):
    """The cloud could not be reached successfully."""


class ProtocolError(AlorairError):
    """The response or requested identity does not match the supported protocol."""


class CommandError(AlorairError):
    """A command failed or its delivery could not be confirmed."""

    def __init__(self, message: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


def _vendor_rejection(error: AlorairError, code: int | None) -> AlorairError:
    if type(code) is int and 0 <= code <= 999_999_999:
        error.vendor_code = code
        error.args = (f"{error} Vendor response code: {code}.",)
    return error


def normalize_mac(value: Any) -> str:
    """Normalize a complete MAC; never accept a substring or partial match."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9a-fA-F]{12}|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|(?:[0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2}",
        value,
    ):
        raise ProtocolError("A complete device MAC address is required.")
    return value.replace(":", "").replace("-", "").upper()


def _device_identity(value: Any) -> str:
    """Validate the full cloud identity separately from the configured MAC."""
    if isinstance(value, str) and re.fullmatch(r"0{12}[0-9a-fA-F]{12}", value):
        return value.upper()
    return normalize_mac(value)


def _device_mac(value: Any) -> str:
    return _device_identity(value)[-12:].upper()


def _identifier(value: Any) -> str:
    if type(value) not in {str, int} or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value)):
        raise ProtocolError("The vendor returned an invalid identity.")
    return str(value)


def _numeric_code(value: Any) -> int | None:
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
        return int(value)
    return None


def _encrypt_password(password: str, public_key: bytes) -> str:
    try:
        key = serialization.load_pem_public_key(public_key)
        if not isinstance(key, rsa.RSAPublicKey):
            raise TypeError
        encoded = password.encode("utf-8")
        if not encoded or len(encoded) > key.key_size // 8 - 11:
            raise ValueError
        return base64.b64encode(key.encrypt(encoded, padding.PKCS1v15())).decode("ascii")
    except ValueError, TypeError, UnicodeError:
        raise AuthenticationError("Credentials cannot be encoded for the vendor login.") from None


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?",
        value,
    ):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _experimental_number(value: Any, low: float = 0, high: float = 1_000_000_000) -> float:
    if (
        type(value) not in {int, float, str}
        or isinstance(value, str)
        and not re.fullmatch(r"-?[0-9]{1,10}(?:\.[0-9]{1,8})?", value)
    ):
        raise ProtocolError("Experimental response contains an unsupported numeric field.")
    try:
        number = float(value)
    except ValueError, OverflowError:
        raise ProtocolError("Experimental response contains an unsupported numeric field.") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ProtocolError("Experimental response contains an out-of-range numeric field.")
    return number


def _experimental_bool(value: Any) -> bool:
    if type(value) is bool:
        return value
    if type(value) is int and value in {0, 1}:
        return bool(value)
    raise ProtocolError("Experimental response contains an unsupported boolean field.")


def _experimental_date(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        if _timestamp(value + " 00:00:00") is not None:
            return value
    raise CommandError("A valid query_date in YYYY-MM-DD format is required.")


def _experimental_parameters(action: str, parameters: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "history": {"period", "query_date"},
        "operation_time": {"period", "query_date"},
        "filters": set(),
        "filter_detail": {"filter_id"},
        "firmware_check": set(),
        "firmware_history": {"page"},
        "rename": {"label"},
        "location": {"label"},
        "extend_filter": {"filter_id", "extension"},
        "reset_filter": {"filter_id"},
    }
    if not isinstance(action, str) or action not in allowed or parameters.keys() - allowed[action]:
        raise CommandError("Unsupported experimental action or parameters.")
    result = parameters.copy()
    if action in {"history", "operation_time"}:
        if not isinstance(result.get("period"), str) or result["period"] not in {"day", "month", "year"}:
            raise CommandError("History period must be day, month, or year.")
        result["query_date"] = _experimental_date(result.get("query_date"))
    if action == "firmware_history":
        page = result.setdefault("page", 1)
        if type(page) is not int or not 1 <= page <= 10:
            raise CommandError("Firmware history page must be an integer from 1 to 10.")
    if action in {"filter_detail", "extend_filter", "reset_filter"}:
        result["filter_id"] = _identifier(result.get("filter_id"))
    if action == "extend_filter" and (type(result.get("extension")) is not int or result["extension"] not in {1, 2, 3}):
        raise CommandError("Filter extension must be 1 (week), 2 (month), or 3 (three months).")
    if action in {"rename", "location"}:
        label = result.get("label")
        limit = 64 if action == "rename" else 128
        if (
            not isinstance(label, str)
            or len(label) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in label)
            or action == "rename"
            and not label.strip()
        ):
            raise CommandError("The label is empty, too long, or contains unsupported control characters.")
        try:
            if len(label.encode("utf-8")) > 256:
                raise ValueError
        except ValueError, UnicodeError:
            raise CommandError("The label cannot be encoded within the supported bound.") from None
    return result


def _experimental_filters(detail: dict[str, Any]) -> list[dict[str, Any]]:
    filters = detail.get("filter")
    if not isinstance(filters, list) or len(filters) > MAX_FILTERS:
        raise ProtocolError("The device did not provide a supported bounded filter list.")
    result, seen = [], set()
    for item in filters:
        if not isinstance(item, dict):
            raise ProtocolError("The vendor returned an unsupported filter entry.")
        filter_id = _identifier(item.get("id"))
        if filter_id in seen or re.fullmatch(r"(?:0{12})?[A-Fa-f0-9]{12}", filter_id):
            raise ProtocolError("The vendor returned an unsupported or conflicting filter identity.")
        seen.add(filter_id)
        recommended = _experimental_number(item.get("recommendUseHour"), 0.01)
        used = _experimental_number(item.get("useHour"))
        extension = _experimental_number(item["extendUseHour"] if item.get("extendUseHour") is not None else 0)
        remaining = max(recommended - used, 0)
        result.append(
            {
                "filter_id": filter_id,
                "recommended_hours": recommended,
                "used_hours": used,
                "extension_hours": extension,
                "remaining_hours": remaining,
                "remaining_days": round(remaining / 24, 2),
                "remaining_percent": round(100 * remaining / recommended, 2),
                "extension_days": round(max(recommended + extension - used, 0) / 24, 2) if extension > 0 else 0,
            }
        )
    return result


def _public_device(device: dict[str, Any]) -> dict[str, Any]:
    _device_identity(device["deviceNum"])
    result: dict[str, Any] = {
        "id": device["id"],
        "deviceNum": device["deviceNum"],
    }
    for field in STATUS_FIELDS:
        value = device.get(field)
        if (
            isinstance(value, bool)
            or type(value) in {int, float}
            and -1e12 < value < 1e12
            and math.isfinite(value)
            or isinstance(value, str)
            and re.fullmatch(r"[0-9A-Fa-f:. -]{1,32}", value)
        ):
            result[field] = value
    for field in METADATA_FIELDS:
        value = device.get(field)
        if isinstance(value, str) and 0 < len(value) <= 128 and not any(ord(char) < 32 for char in value):
            result[field] = value
    updated = _timestamp(device.get("updateTimeStr"))
    if updated is not None:
        result["updateTimeStr"] = updated
    return result


class AlorairClient:
    """Owned-device client; callers retain ownership of the supplied session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        allow_insecure_http: bool = False,
        base_url: str = DEFAULT_BASE_URL,
        public_key: bytes = PUBLIC_KEY,
        timeout: float = 20.0,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ProtocolError("Invalid cloud API base URL.")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ProtocolError("The vendor uses unencrypted HTTP; explicit transport consent is required.")
        if session.trust_env:
            raise ProtocolError("Use a session with trust_env=False to keep credentials out of environment proxies.")
        if not isinstance(username, str) or not username or len(username) > 320 or not isinstance(password, str):
            raise AuthenticationError("An account username and password are required.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ProtocolError("The request timeout must be finite and positive.")
        self._session = session
        self._username = username
        self._password = password
        self._public_key = public_key
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._token: str | None = None
        self._owner_id: str | None = None
        self._auth_generation = 0
        self._auth_failure: tuple[type[AlorairError], str] | None = None
        self._login_lock = asyncio.Lock()
        self._devices_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._devices: dict[str, dict[str, Any]] = {}

    def __repr__(self) -> str:
        return f"<AlorairClient authenticated={self._token is not None}>"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        stage = {
            "user/login": "login",
            "user/getUserInfo": "user_info",
            "iot/device/getHistoryDeviceList": "device_list",
            "iot/device/control": "control",
        }.get(path, "device_detail" if path.startswith("iot/device/getDeviceDetail/") else "request")
        try:
            return await self._request_data(method, path, token=token, payload=payload)
        except AlorairError as err:
            err.diagnostic_stage = stage
            raise

    async def _request_data(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        command = method == "POST" and (
            path == "iot/device/control"
            or path in _EXPERIMENTAL_POST_PATHS
            or re.fullmatch(r"iot/device/resetFilter/[A-Za-z0-9_-]{1,128}/[A-Za-z0-9_-]{1,128}", path)
        )
        allowed = (
            command
            or method == "POST"
            and path == "user/login"
            or method == "GET"
            and (
                path in {"user/getUserInfo", "iot/device/getHistoryDeviceList"} | _EXPERIMENTAL_GET_PATHS
                or re.fullmatch(r"iot/device/getDeviceDetail/[A-Za-z0-9_-]{1,128}", path)
                or re.fullmatch(r"iot/device/checkDeviceNeedUpgrade/[A-Za-z0-9_-]{1,128}", path)
                or re.fullmatch(r"iot/filter/getApiFilterDetail/[A-Za-z0-9_-]{1,128}", path)
            )
        )
        if not allowed:
            raise ProtocolError("Unsupported cloud endpoint.")
        headers = {
            "env": "app",
            "platform": "1",
            "lang": "en",
            "App-Identity": "1",
            "Accept": "application/json",
        }
        if token is not None:
            if not re.fullmatch(r"[!-~]{1,8192}", token):
                raise AuthenticationError("The vendor returned an invalid session token.")
            headers["token"] = token
        try:
            async with self._session.request(
                method,
                self._base_url + path,
                headers=headers,
                json=payload if method == "POST" else None,
                params=payload if method == "GET" else None,
                allow_redirects=False,
                raise_for_status=False,
                timeout=self._timeout,
            ) as response:
                if response.status in {401, 403}:
                    raise AuthenticationError("The vendor rejected the session or credentials.")
                if 300 <= response.status < 400:
                    if command:
                        raise CommandError(
                            "Command response redirected; delivery is unconfirmed and was not retried.",
                            outcome_unknown=True,
                        )
                    raise ProtocolError("Cloud redirect refused; credentials were not forwarded.")
                if response.status != 200:
                    if command:
                        raise CommandError(
                            "The vendor returned a command HTTP error; the command was not retried.",
                            outcome_unknown=response.status >= 500,
                        )
                    raise CannotConnect("The vendor returned an unsuccessful HTTP status.")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        if command:
                            raise CommandError(
                                "Command response exceeded the limit; delivery is unconfirmed.",
                                outcome_unknown=True,
                            )
                        raise ProtocolError("Cloud response exceeded the size limit.")
                try:
                    result = json.loads(body)
                except ValueError, UnicodeError:
                    if command:
                        raise CommandError(
                            "Command response was invalid; delivery is unconfirmed and was not retried.",
                            outcome_unknown=True,
                        ) from None
                    raise ProtocolError("The vendor returned invalid JSON.") from None
        except aiohttp.ClientError, TimeoutError, OSError:
            if command:
                raise CommandError(
                    "Command delivery could not be confirmed; it was not retried.",
                    outcome_unknown=True,
                ) from None
            raise CannotConnect("The cloud request could not be completed.") from None
        if not isinstance(result, dict):
            if command:
                raise CommandError(
                    "Command acknowledgement was invalid; delivery is unconfirmed.",
                    outcome_unknown=True,
                )
            raise ProtocolError("The vendor returned an unsupported response.")
        code = _numeric_code(result.get("code"))
        if code is not None and code >= 10000:
            raise _vendor_rejection(AuthenticationError("The vendor rejected the session or credentials."), code)
        if code != 200:
            if command:
                raise CommandError(
                    "The vendor did not acknowledge the command.",
                    outcome_unknown=code is None,
                )
            raise _vendor_rejection(
                ProtocolError("The vendor rejected the request or returned an unsupported response."), code
            )
        return result.get("data")

    async def _authenticate(self) -> None:
        self._token = None
        self._owner_id = None
        self._devices = {}
        self._auth_failure = None
        try:
            password = await asyncio.to_thread(_encrypt_password, self._password, self._public_key)
            data = await self._request(
                "POST",
                "user/login",
                payload={"username": self._username, "password": password, "method": 1},
            )
            token = data.get("token") if isinstance(data, dict) else None
            if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,8192}", token):
                raise AuthenticationError("The vendor login did not return a usable session.", stage="login")
            user = await self._request("GET", "user/getUserInfo", token=token)
            if not isinstance(user, dict):
                raise ProtocolError("The vendor did not return an account identity.", stage="user_info")
            try:
                self._owner_id = _identifier(user.get("uid"))
            except ProtocolError:
                raise ProtocolError("The vendor returned an invalid account identity.", stage="user_info") from None
            self._token = token
        except AlorairError as exc:
            self._auth_failure = (type(exc), str(exc))
            raise
        finally:
            self._auth_generation += 1

    async def async_login(self) -> None:
        """Authenticate once, coalescing concurrent attempts."""
        generation = self._auth_generation
        async with self._login_lock:
            if self._token is not None:
                return
            if generation != self._auth_generation and self._auth_failure:
                error, message = self._auth_failure
                raise error(message)
            await self._authenticate()

    async def _refresh(self, rejected_token: str | None, generation: int) -> None:
        async with self._login_lock:
            if self._token is not None and (self._token != rejected_token or generation != self._auth_generation):
                return
            if generation != self._auth_generation and self._auth_failure:
                error, message = self._auth_failure
                raise error(message)
            await self._authenticate()

    async def _read(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        await self.async_login()
        token = self._token
        generation = self._auth_generation
        try:
            return await self._request("GET", path, token=token, payload=payload)
        except AuthenticationError:
            await self._refresh(token, generation)
            # Exactly one authentication refresh per read, never an unbounded retry loop.
            return await self._request("GET", path, token=self._token, payload=payload)

    async def _fetch_devices(self) -> dict[str, dict[str, Any]]:
        try:
            return await self._fetch_device_pages()
        except ProtocolError as err:
            if err.diagnostic_stage == "request":
                err.diagnostic_stage = "device_list"
            raise

    async def _fetch_device_pages(self) -> dict[str, dict[str, Any]]:
        devices: dict[str, dict[str, Any]] = {}
        for page in range(1, MAX_PAGES + 1):
            data = await self._read(
                "iot/device/getHistoryDeviceList",
                {"pageNum": page, "pageSize": PAGE_SIZE},
            )
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise ProtocolError("The vendor returned an unsupported device list.")
            total = _numeric_code(data.get("totalPage"))
            if total is None or not 0 <= total <= MAX_PAGES or len(data["list"]) > PAGE_SIZE:
                raise ProtocolError("Device pagination exceeded the supported bounds.")
            for device in data["list"]:
                if not isinstance(device, dict):
                    raise ProtocolError("The vendor returned an invalid device entry.")
                # Live history omits userId. A row is only a candidate; detail
                # must prove ownership before status is exposed or control sent.
                if device.get("userId") is not None and _identifier(device["userId"]) != self._owner_id:
                    continue
                mac = _device_mac(device.get("deviceNum"))
                device_id = _identifier(device.get("id"))
                if mac in devices and (
                    _identifier(devices[mac]["id"]) != device_id
                    or _device_identity(devices[mac]["deviceNum"]) != _device_identity(device["deviceNum"])
                ):
                    raise ProtocolError("The vendor returned conflicting device identities.")
                devices[mac] = device.copy()
            if page >= total:
                return devices
        raise ProtocolError("Device pagination did not terminate.")

    async def async_devices(self) -> list[dict[str, Any]]:
        """Return this account's owned devices, excluding guest history entries."""
        await self.async_login()
        async with self._devices_lock:
            self._devices = await self._fetch_devices()
            candidates = list(self._devices.values())
        owned = []
        for candidate in candidates:
            detail = await self._device_detail(candidate)
            if detail.get("userId") is not None and _identifier(detail["userId"]) == self._owner_id:
                owned.append(_public_device({"id": candidate["id"], **detail}))
        return owned

    async def _resolve(self, mac: str) -> dict[str, Any]:
        target = normalize_mac(mac)
        await self.async_login()
        async with self._devices_lock:
            if target not in self._devices:
                self._devices = await self._fetch_devices()
            device = self._devices.get(target)
            if device is None:
                raise ProtocolError(
                    "The exact MAC was not found among this account's owned devices.", stage="device_list"
                )
            return device.copy()

    async def _device_detail(self, device: dict[str, Any]) -> dict[str, Any]:
        device_id = _identifier(device["id"])
        detail = await self._read("iot/device/getDeviceDetail/" + device_id)
        try:
            if (
                not isinstance(detail, dict)
                or _device_identity(detail.get("deviceNum")) != _device_identity(device["deviceNum"])
                or "id" in detail
                and _identifier(detail["id"]) != device_id
            ):
                raise ProtocolError("Device detail did not match the requested identity.")
        except ProtocolError as err:
            err.diagnostic_stage = "device_detail"
            raise
        return detail

    async def _owned_device(self, mac: str) -> dict[str, Any]:
        device = await self._resolve(normalize_mac(mac))
        detail = await self._device_detail(device)
        if detail.get("userId") is None or _identifier(detail["userId"]) != self._owner_id:
            raise ProtocolError("Device ownership could not be verified.", stage="device_detail")
        # Detail may omit id. Restore the validated GET-route identity only after
        # the full cloud number and account ownership have both been verified.
        return {"id": device["id"], **detail}

    async def async_status(self, mac: str) -> dict[str, Any]:
        """Resolve the exact MAC, then verify detail identity and account ownership."""
        result = _public_device(await self._owned_device(mac))
        result["observed_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        return result

    def _experimental_text(self, value: Any, pattern: str, limit: int) -> str:
        if not isinstance(value, str) or not 0 < len(value) <= limit or not re.fullmatch(pattern, value):
            raise ProtocolError("Experimental response contains an unsupported text field.")
        if any(
            secret and secret.casefold() in value.casefold() for secret in (self._username, self._password, self._token)
        ) or re.fullmatch(r"(?:0{12})?[A-Fa-f0-9]{12}", value):
            raise ProtocolError("Experimental response contains a private or unsupported text field.")
        return value

    def _experimental_filter_summaries(self, detail: dict[str, Any]) -> list[dict[str, Any]]:
        result = _experimental_filters(detail)
        for item in result:
            self._experimental_text(item["filter_id"], r"[A-Za-z0-9_-]+", 128)
        return result

    @staticmethod
    def _experimental_response_identity(data: dict[str, Any], detail: dict[str, Any]) -> None:
        if "deviceNum" in data and _device_identity(data["deviceNum"]) != _device_identity(detail["deviceNum"]):
            raise ProtocolError("Experimental response did not match the owned device identity.")
        if "deviceId" in data and _identifier(data["deviceId"]) != _identifier(detail["id"]):
            raise ProtocolError("Experimental response did not match the owned device identity.")

    async def async_experimental_action(self, mac: str, action: str, **parameters: Any) -> dict[str, Any]:
        """Execute one explicitly allowlisted action and return a sanitized result."""
        parameters = _experimental_parameters(action, parameters)
        target = normalize_mac(mac)
        async with self._command_lock:
            detail = await self._owned_device(target)
            if action in EXPERIMENTAL_MUTATION_ACTIONS:
                return await self._experimental_mutation(target, action, parameters, detail)
            return await self._experimental_read(action, parameters, detail)

    async def _experimental_read(
        self, action: str, parameters: dict[str, Any], detail: dict[str, Any]
    ) -> dict[str, Any]:
        if action == "filters":
            return {"filters": self._experimental_filter_summaries(detail)}
        if action in {"history", "operation_time"}:
            query = {
                "deviceNum": detail["deviceNum"],
                "type": {"day": 1, "month": 2, "year": 3}[parameters["period"]],
                "queryDate": parameters["query_date"],
            }
            path = "getDeviceHistoryDateList" if action == "history" else "getDeviceHistoryTime"
            data = await self._read("iot/device/" + path, query)
            if action == "operation_time":
                if not isinstance(data, dict):
                    raise ProtocolError("The vendor returned an unsupported operation-time summary.")
                self._experimental_response_identity(data, detail)
                return {"hours": _experimental_number(data.get("useTime"))}
            if not isinstance(data, list) or len(data) > MAX_HISTORY_SAMPLES:
                raise ProtocolError("The vendor returned an unsupported or oversized history list.")
            samples = []
            for row in data:
                if not isinstance(row, dict) or (sampled := _timestamp(row.get("lastTime"))) is None:
                    raise ProtocolError("The vendor returned an unsupported history sample.")
                self._experimental_response_identity(row, detail)
                sample: dict[str, Any] = {"sample_time": sampled}
                for field, output in _HISTORY_FIELDS.items():
                    if row.get(field) is not None:
                        sample[output] = _experimental_number(row[field], -1000, 10000)
                if len(sample) == 1:
                    raise ProtocolError("The history sample did not contain supported measurements.")
                samples.append(sample)
            return {"samples": samples}
        if action == "filter_detail":
            selected = self._experimental_filter(detail, parameters["filter_id"])
            data = await self._read("iot/filter/getApiFilterDetail/" + selected["filter_id"])
            if not isinstance(data, dict):
                raise ProtocolError("The vendor returned unsupported filter replacement metadata.")
            if "id" in data and _identifier(data["id"]) != selected["filter_id"]:
                raise ProtocolError("The filter metadata did not match the owned filter identity.")
            result: dict[str, Any] = {"filter_id": selected["filter_id"]}
            if data.get("discountRatio") is not None:
                result["discount_percent"] = _experimental_number(data["discountRatio"], 0, 100)
            if data.get("discountCode"):
                result["discount_code"] = self._experimental_text(data["discountCode"], r"[A-Za-z0-9_-]+", 32)
            if len(result) == 1:
                raise ProtocolError("The filter metadata did not contain supported replacement fields.")
            return result
        if action == "firmware_check":
            data = await self._read("iot/device/checkDeviceNeedUpgrade/" + _identifier(detail["id"]))
            if not isinstance(data, dict):
                raise ProtocolError("The vendor returned unsupported firmware availability metadata.")
            self._experimental_response_identity(data, detail)
            return {"update_available": _experimental_bool(data.get("isUpgrade"))}
        data = await self._read(
            "iot/firmware/getUpgradeListByDevice/",
            {
                "deviceId": detail["id"],
                "pageNum": parameters["page"],
                "pageSize": 20,
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("list"), list) or len(data["list"]) > 20:
            raise ProtocolError("The vendor returned unsupported firmware history metadata.")
        self._experimental_response_identity(data, detail)
        total = _numeric_code(data.get("totalRow"))
        if total is None or not 0 <= total <= 1_000_000:
            raise ProtocolError("Firmware history count exceeded supported bounds.")
        rows = []
        for row in data["list"]:
            if not isinstance(row, dict) or (updated := _timestamp(row.get("updateTime"))) is None:
                raise ProtocolError("The vendor returned an unsupported firmware history row.")
            self._experimental_response_identity(row, detail)
            rows.append(
                {
                    "version": self._experimental_text(
                        row.get("firmwareVersion"), r"[vV]?[0-9]+(?:\.[A-Za-z0-9]+){0,7}(?:[-+][A-Za-z0-9.-]+)?", 64
                    ),
                    "is_upgrade": _experimental_bool(row.get("isUpgrade")),
                    "updated_at": updated,
                }
            )
        return {"page": parameters["page"], "total_rows": total, "versions": rows}

    def _experimental_filter(self, detail: dict[str, Any], filter_id: str) -> dict[str, Any]:
        for item in self._experimental_filter_summaries(detail):
            if item["filter_id"] == filter_id:
                return item
        raise ProtocolError("The selected filter does not belong to the exact owned device.")

    async def _experimental_mutation(
        self, mac: str, action: str, parameters: dict[str, Any], detail: dict[str, Any]
    ) -> dict[str, Any]:
        requests: list[tuple[str, dict[str, Any]]] = []
        before: dict[str, Any] | None = None
        if action == "rename":
            requests = [
                (
                    "iot/device/control",
                    {
                        "deviceId": detail["id"],
                        "deviceNum": detail["deviceNum"],
                        "frameType": "29",
                        "data": parameters["label"],
                    },
                ),
                ("iot/device/updateDeviceName", {"id": detail["id"], "name": parameters["label"]}),
            ]
        elif action == "location":
            requests = [("iot/device/updateDeviceLocation", {"id": detail["id"], "location": parameters["label"]})]
        else:
            before = self._experimental_filter(detail, parameters["filter_id"])
            if action == "extend_filter":
                # Match the app's rounded-percent display gate, not a raw-hour guess.
                if math.floor(100 * before["remaining_hours"] / before["recommended_hours"] + 0.5) >= 20:
                    raise CommandError("Filter extension is available only below 20 percent remaining life.")
                requests = [
                    (
                        "iot/device/updateFilterHour",
                        {
                            "id": detail["id"],
                            "filterId": parameters["filter_id"],
                            "extendUseHour": parameters["extension"],
                        },
                    )
                ]
            else:
                path = "iot/device/resetFilter/" + _identifier(detail["id"]) + "/" + parameters["filter_id"]
                requests = [(path, {})]
        acknowledged = False
        try:
            for path, payload in requests:
                await self._request("POST", path, token=self._token, payload=payload)
                acknowledged = True
            feedback = await self._owned_device(mac)
            if action in {"rename", "location"}:
                field = "name" if action == "rename" else "location"
                return {"accepted": True, "confirmed": feedback.get(field) == parameters["label"]}
            after = self._experimental_filter(feedback, parameters["filter_id"])
            if action == "extend_filter":
                confirmed = before is not None and after["extension_hours"] != before["extension_hours"]
            else:
                # Reset-to-full is a zero used-hour counter after a positive
                # baseline. Ordinary usage increments cannot confirm a reset.
                confirmed = before is not None and before["used_hours"] > 0 and after["used_hours"] == 0
            return {"accepted": True, "confirmed": confirmed, "filter": after}
        except AlorairError as err:
            if acknowledged:
                raise CommandError(
                    "Experimental mutation was partially acknowledged; final state is uncertain "
                    "and no request was replayed.",
                    outcome_unknown=True,
                ) from None
            raise err

    async def _command(self, mac: str, frame_type: str | int, value: str) -> None:
        async with self._command_lock:
            detail = await self.async_status(mac)
            payload: dict[str, Any] = {
                "deviceId": detail["id"],
                "frameType": frame_type,
                "data": value,
            }
            if frame_type not in (24, 25):
                payload["deviceNum"] = detail["deviceNum"]
            # No auth or transport retry on a command: timeout may mean it was executed.
            await self._request("POST", "iot/device/control", token=self._token, payload=payload)

    async def async_set_power(self, mac: str, on: bool) -> None:
        if type(on) is not bool:
            raise CommandError("Power state must be a boolean.")
        await self._command(mac, "21", "01" if on else "00")

    async def async_set_humidity(self, mac: str, humidity: int) -> None:
        if type(humidity) is not int or humidity not in range(20, 81, 5):
            raise CommandError("Humidity must be 20 (continuous) or 25–80 in steps of five.")
        await self._command(mac, "23", str(humidity))

    async def async_purge(self, mac: str) -> None:
        await self._command(mac, "22", "01")

    async def async_set_locate(self, mac: str, on: bool) -> None:
        if type(on) is not bool:
            raise CommandError("Locate state must be a boolean.")
        await self._command(mac, "27", "01" if on else "00")

    async def async_set_temperature_unit(self, mac: str, unit: str) -> None:
        if unit not in ("celsius", "fahrenheit"):
            raise CommandError("Temperature unit must be celsius or fahrenheit.")
        await self._command(mac, 24, "01" if unit == "fahrenheit" else "00")

    async def async_set_moisture_unit(self, mac: str, unit: str) -> None:
        if unit not in ("grains_per_pound", "grams_per_kilogram"):
            raise CommandError("Moisture unit must be grains_per_pound or grams_per_kilogram.")
        await self._command(mac, 25, "01" if unit == "grams_per_kilogram" else "00")
