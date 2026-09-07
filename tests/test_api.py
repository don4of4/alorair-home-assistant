"""Exercise the cloud transport against loopback servers without Home Assistant."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

# Loading the module directly keeps these tests independent of HA package startup.
_spec = importlib.util.spec_from_file_location(
    "alorair_api_test_module",
    Path(__file__).parents[1] / "custom_components/alorair_lite/api.py",
)
assert _spec is not None and _spec.loader is not None
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)
MAC = "AABBCCDDEE01"
OTHER_MAC = "AABBCCDDEE02"
PASSWORD = "PRIVATE_TEST_PASSWORD"
TOKEN = "PRIVATE_TEST_TOKEN"
USERNAME = "private@example.invalid"


@pytest.fixture(scope="module")
def rsa_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


@asynccontextmanager
async def server(handler):
    app = web.Application()

    async def route(request):
        return await handler(request)

    app.router.add_route("*", "/{path:.*}", route)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=0.1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/rest/api/"
    finally:
        await runner.cleanup()


def response(data=None, *, code=200):
    return web.json_response({"code": code, "data": data})


class Vendor:
    def __init__(self):
        self.requests = []
        self.logins = 0
        self.commands = []
        self.pages = [
            [
                {"id": 90, "deviceNum": OTHER_MAC, "userId": "other"},
                {"id": 91, "deviceNum": "ABCDEF123456"},
            ],
            [
                {
                    "id": 42,
                    "deviceNum": "aa:bb:cc:dd:ee:01",
                    "userId": 7,
                    "name": "Basement",
                }
            ],
        ]
        self.detail = {
            "id": 42,
            "deviceNum": MAC,
            "userId": 7,
            "name": "Basement",
            "productName": "Storm Pro",
            "powerStatus": "01",
            "inHumidity": 61,
            "currentHumidity": "45",
            "errCode": "00",
            "singleWorktime": 120,
            "inGkg": 11.1,
            "coilTemperature": 20,
            "updateTimeStr": "2026-09-07 12:34:56",
            "token": TOKEN,
            "wifiPassword": PASSWORD,
            "location": "PRIVATE_LOCATION",
            "username": USERNAME,
        }
        self.expired = False
        self.still_expired = False
        self.reject_commands = False
        self.command_delay = 0
        self.login_bodies = []

    async def __call__(self, request):
        self.requests.append((request.method, request.path, dict(request.query)))
        if request.path.endswith("user/login"):
            self.logins += 1
            self.login_bodies.append(await request.json())
            await asyncio.sleep(0.01)
            return response({"token": TOKEN if self.logins == 1 else "REFRESHED_TOKEN"})
        assert request.headers["env"] == "app"
        assert request.headers["platform"] == "1"
        assert request.headers["App-Identity"] == "1"
        if request.path.endswith("user/getUserInfo"):
            return response({"uid": 7, "username": USERNAME, "password": PASSWORD})
        if self.still_expired or self.expired and request.headers.get("token") == TOKEN:
            return response({"password": PASSWORD}, code=10001)
        if request.path.endswith("getHistoryDeviceList"):
            assert request.query["pageSize"] == "10"
            page = int(request.query["pageNum"])
            return response({"list": self.pages[page - 1], "totalPage": len(self.pages)})
        if "/getDeviceDetail/" in request.path:
            if request.path.endswith("/91"):
                return response({"id": 91, "deviceNum": "ABCDEF123456", "userId": "other"})
            assert request.path.endswith("/42")
            return response(self.detail)
        if request.path.endswith("iot/device/control"):
            self.commands.append(await request.json())
            if self.reject_commands:
                return response({"password": PASSWORD}, code=10001)
            await asyncio.sleep(self.command_delay)
            return response(None)
        raise AssertionError("Unexpected endpoint")


@asynccontextmanager
async def client_vendor(rsa_keys, *, request_timeout=1):
    vendor = Vendor()
    async with server(vendor) as url, aiohttp.ClientSession() as session:
        client = api.AlorairClient(
            session,
            USERNAME,
            PASSWORD,
            allow_insecure_http=True,
            base_url=url,
            public_key=rsa_keys[1],
            timeout=request_timeout,
        )
        yield client, vendor


@pytest.mark.asyncio
async def test_login_rsa_owned_pagination_and_exact_status_redaction(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        await client.async_login()
        body = vendor.login_bodies[0]
        assert body["username"] == USERNAME and body["method"] == 1
        assert PASSWORD not in json.dumps(body)
        assert rsa_keys[0].decrypt(base64.b64decode(body["password"]), padding.PKCS1v15()).decode() == PASSWORD
        devices = await client.async_devices()
        assert len(devices) == 1 and devices[0]["deviceNum"] == MAC
        before = datetime.now(UTC).replace(microsecond=0)
        status = await client.async_status("aa:bb:cc:dd:ee:01")
        assert status["id"] == 42 and status["inHumidity"] == 61
        assert status["singleWorktime"] == 120 and status["inGkg"] == 11.1
        assert status["updateTimeStr"] == "2026-09-07 12:34:56"
        observed = datetime.fromisoformat(status["observed_at_utc"].replace("Z", "+00:00"))
        assert before <= observed <= datetime.now(UTC)
        assert "userId" not in status
        for secret in (PASSWORD, TOKEN, USERNAME, "PRIVATE_LOCATION"):
            assert secret not in json.dumps(status) + json.dumps(devices) + repr(client)
        assert vendor.logins == 1
        assert [query["pageNum"] for _, path, query in vendor.requests if path.endswith("getHistoryDeviceList")] == [
            "1",
            "2",
        ]


@pytest.mark.asyncio
async def test_concurrent_initial_login_and_expired_read_refresh_are_coalesced(
    rsa_keys,
):
    async with client_vendor(rsa_keys) as (client, vendor):
        await asyncio.gather(*(client.async_login() for _ in range(10)))
        assert vendor.logins == 1
        await client.async_devices()
        vendor.expired = True
        results = await asyncio.gather(*(client.async_status(MAC) for _ in range(10)))
        assert all(result["deviceNum"] == MAC for result in results)
        assert vendor.logins == 2


@pytest.mark.asyncio
async def test_rejected_refresh_stops_after_one_retry(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        await client.async_devices()
        vendor.still_expired = True
        with pytest.raises(api.AuthenticationError) as exc:
            await client.async_status(MAC)
        assert vendor.logins == 2
        assert TOKEN not in str(exc.value) and PASSWORD not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [{"deviceNum": OTHER_MAC}, {"id": 43}, {"userId": 8}, {"deviceNum": MAC + "00"}],
)
async def test_status_identity_and_owner_mismatch_block_controls(rsa_keys, change):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.detail.update(change)
        with pytest.raises(api.ProtocolError):
            await client.async_set_power(MAC, True)
        assert vendor.commands == []


@pytest.mark.asyncio
async def test_guest_device_and_partial_mac_are_never_controlled(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        for mac in (OTHER_MAC, MAC[-6:], MAC + "00"):
            with pytest.raises(api.ProtocolError):
                await client.async_set_power(mac, True)
        assert vendor.commands == []
        assert not any("getDeviceDetail" in path for _, path, _ in vendor.requests)


@pytest.mark.asyncio
async def test_all_control_bodies_use_exact_app_opcode_types_and_payloads(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        await client.async_set_power(MAC, True)
        await client.async_set_power(MAC, False)
        await client.async_set_humidity(MAC, 45)
        await client.async_set_humidity(MAC, 20)
        await client.async_purge(MAC)
        await client.async_set_locate(MAC, True)
        await client.async_set_locate(MAC, False)
        await client.async_set_temperature_unit(MAC, "celsius")
        await client.async_set_temperature_unit(MAC, "fahrenheit")
        await client.async_set_moisture_unit(MAC, "grains_per_pound")
        await client.async_set_moisture_unit(MAC, "grams_per_kilogram")
        expected = [
            ("21", "01"),
            ("21", "00"),
            ("23", "45"),
            ("23", "20"),
            ("22", "01"),
            ("27", "01"),
            ("27", "00"),
            (24, "00"),
            (24, "01"),
            (25, "00"),
            (25, "01"),
        ]
        assert len(vendor.commands) == len(expected)
        for command, (frame, data) in zip(vendor.commands, expected, strict=True):
            wanted = {"deviceId": 42, "frameType": frame, "data": data}
            if type(frame) is str:
                wanted["deviceNum"] = MAC
            assert command == wanted
        assert vendor.detail["powerStatus"] == "01"  # No optimistic status mutation.


@pytest.mark.asyncio
async def test_invalid_control_values_fail_before_any_network(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        for value in (True, "45", 45.0, 21, 0, 85):
            with pytest.raises(api.CommandError):
                await client.async_set_humidity(MAC, value)
        with pytest.raises(api.CommandError):
            await client.async_set_power(MAC, "true")
        with pytest.raises(api.CommandError):
            await client.async_set_temperature_unit(MAC, "kelvin")
        with pytest.raises(api.CommandError):
            await client.async_set_moisture_unit(MAC, "percent")
        assert vendor.requests == []


@pytest.mark.asyncio
async def test_ambiguous_command_timeout_is_not_replayed(rsa_keys):
    async with client_vendor(rsa_keys, request_timeout=0.05) as (client, vendor):
        await client.async_devices()
        vendor.command_delay = 0.2
        with pytest.raises(api.CommandError) as exc:
            await client.async_purge(MAC)
        assert exc.value.outcome_unknown
        assert "not retried" in str(exc.value)
        await asyncio.sleep(0.25)
        assert len(vendor.commands) == 1 and vendor.logins == 1


@pytest.mark.asyncio
async def test_command_auth_failure_is_not_replayed_or_reauthenticated(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.reject_commands = True
        with pytest.raises(api.AuthenticationError):
            await client.async_set_power(MAC, False)
        assert len(vendor.commands) == 1 and vendor.logins == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
async def test_redirects_do_not_forward_login_or_tokens(rsa_keys, code):
    sink_requests = []

    async def sink(request):
        sink_requests.append(request.path)
        return response()

    async with server(sink) as sink_url:

        async def redirect(request):
            return web.Response(status=code, headers={"Location": sink_url + "leak"})

        async with server(redirect) as url, aiohttp.ClientSession() as session:
            client = api.AlorairClient(
                session,
                USERNAME,
                PASSWORD,
                allow_insecure_http=True,
                base_url=url,
                public_key=rsa_keys[1],
            )
            with pytest.raises(api.ProtocolError):
                await client.async_login()
            with pytest.raises(api.ProtocolError):
                await client._request("GET", "user/getUserInfo", token=TOKEN)
        assert sink_requests == []


@pytest.mark.asyncio
async def test_response_limit_and_invalid_json_do_not_expose_body(rsa_keys):
    for body in (PASSWORD.encode(), b"x" * (api.MAX_RESPONSE_BYTES + 1)):

        async def malformed(request, value=body):
            return web.Response(body=value)

        async with server(malformed) as url, aiohttp.ClientSession() as session:
            client = api.AlorairClient(
                session,
                USERNAME,
                PASSWORD,
                allow_insecure_http=True,
                base_url=url,
                public_key=rsa_keys[1],
            )
            with pytest.raises(api.ProtocolError) as exc:
                await client.async_login()
            assert PASSWORD not in str(exc.value)


@pytest.mark.asyncio
async def test_http_requires_explicit_consent(rsa_keys):
    async with aiohttp.ClientSession() as session:
        with pytest.raises(api.ProtocolError, match="transport consent"):
            api.AlorairClient(session, USERNAME, PASSWORD)


@pytest.mark.asyncio
async def test_pagination_is_bounded(rsa_keys):
    async def too_many_pages(request):
        if request.path.endswith("user/login"):
            return response({"token": TOKEN})
        if request.path.endswith("user/getUserInfo"):
            return response({"uid": 7})
        return response({"list": [], "totalPage": api.MAX_PAGES + 1})

    async with server(too_many_pages) as url, aiohttp.ClientSession() as session:
        client = api.AlorairClient(
            session,
            USERNAME,
            PASSWORD,
            allow_insecure_http=True,
            base_url=url,
            public_key=rsa_keys[1],
        )
        with pytest.raises(api.ProtocolError, match="pagination"):
            await client.async_devices()


@pytest.mark.asyncio
async def test_concurrent_failed_login_does_not_create_a_login_storm(rsa_keys):
    calls = 0

    async def reject_login(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return response({"password": PASSWORD}, code=10001)

    async with server(reject_login) as url, aiohttp.ClientSession() as session:
        client = api.AlorairClient(
            session,
            USERNAME,
            PASSWORD,
            allow_insecure_http=True,
            base_url=url,
            public_key=rsa_keys[1],
        )
        results = await asyncio.gather(*(client.async_login() for _ in range(10)), return_exceptions=True)
        assert all(isinstance(result, api.AuthenticationError) for result in results)
        assert calls == 1


@pytest.mark.asyncio
async def test_refresh_coalesces_even_if_vendor_reissues_the_same_token(rsa_keys):
    vendor = Vendor()
    rejected_reads = 0
    all_reads_arrived = asyncio.Event()

    async def same_token(request):
        nonlocal rejected_reads
        if request.path.endswith("user/login"):
            vendor.logins += 1
            await request.json()
            await asyncio.sleep(0.01)
            return response({"token": TOKEN})
        if vendor.expired and "/getDeviceDetail/" in request.path and vendor.logins == 1:
            rejected_reads += 1
            if rejected_reads == 10:
                all_reads_arrived.set()
            await asyncio.wait_for(all_reads_arrived.wait(), timeout=1)
            return response(code=10001)
        # Let the fake vendor accept the reused token after refresh.
        vendor.expired = False if vendor.logins > 1 else vendor.expired
        return await vendor(request)

    async with server(same_token) as url, aiohttp.ClientSession() as session:
        client = api.AlorairClient(
            session,
            USERNAME,
            PASSWORD,
            allow_insecure_http=True,
            base_url=url,
            public_key=rsa_keys[1],
        )
        await client.async_devices()
        vendor.expired = True
        results = await asyncio.gather(*(client.async_status(MAC) for _ in range(10)))
        assert all(result["deviceNum"] == MAC for result in results)
        assert vendor.logins == 2


@pytest.mark.asyncio
async def test_invalid_vendor_timestamp_and_status_strings_are_not_exposed(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.detail.update(
            {
                "updateTimeStr": "2026-02-29 12:34:56",
                "inHumidity": PASSWORD,
                "outHumidity": {"token": TOKEN},
            }
        )
        result = await client.async_status(MAC)
        assert "updateTimeStr" not in result
        assert "inHumidity" not in result and "outHumidity" not in result
        assert "observed_at_utc" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, "403", 10001, PASSWORD, 10**30, True, None])
async def test_vendor_rejection_exposes_only_bounded_code_and_fixed_stage(rsa_keys, code):
    async def reject(request):
        return web.json_response(
            {"code": code, "message": PASSWORD, "data": {"token": TOKEN, "deviceNum": MAC, "id": USERNAME}}
        )

    async with server(reject) as url, aiohttp.ClientSession() as session:
        client = api.AlorairClient(
            session, USERNAME, PASSWORD, allow_insecure_http=True, base_url=url, public_key=rsa_keys[1]
        )
        with pytest.raises(api.AlorairError) as caught:
            await client.async_login()
        error = caught.value
        assert error.diagnostic_stage == "login"
        assert error.diagnostic_reason in {"authentication_rejected", "vendor_response_rejected"}
        assert error.vendor_code == ({400: 400, "403": 403, 10001: 10001}.get(code))
        if error.vendor_code is not None:
            assert f"Vendor response code: {error.vendor_code}." in str(error)
        for secret in (PASSWORD, TOKEN, MAC, USERNAME):
            assert secret not in str(error) + error.diagnostic_reason + error.diagnostic_stage


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_request", [False, True])
async def test_account_identity_failure_is_distinct_from_login(rsa_keys, reject_request):
    async def vendor(request):
        if request.path.endswith("user/login"):
            return response({"token": TOKEN})
        return response({"uid": PASSWORD + "!"}, code=403 if reject_request else 200)

    async with server(vendor) as url, aiohttp.ClientSession() as session:
        client = api.AlorairClient(
            session, USERNAME, PASSWORD, allow_insecure_http=True, base_url=url, public_key=rsa_keys[1]
        )
        with pytest.raises(api.ProtocolError) as caught:
            await client.async_login()
        assert caught.value.diagnostic_stage == "user_info"
        assert caught.value.diagnostic_reason == (
            "vendor_response_rejected" if reject_request else "account_identity_invalid"
        )
        assert PASSWORD not in str(caught.value)


@pytest.mark.asyncio
async def test_history_without_owner_uses_only_matching_detail_to_verify_owner(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages[1][0].pop("userId")
        result = await client.async_status(MAC)
        assert result["deviceNum"] == MAC
        detail_paths = [path for _, path, _ in vendor.requests if "/getDeviceDetail/" in path]
        assert len(detail_paths) == 1 and detail_paths[0].endswith("/42")
        await client.async_set_power(MAC, True)
        assert vendor.commands == [{"deviceId": 42, "deviceNum": MAC, "frameType": "21", "data": "01"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [None, 8])
async def test_history_without_owner_never_authorizes_guest_or_missing_detail_owner(rsa_keys, owner):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages[1][0].pop("userId")
        vendor.detail["userId"] = owner
        assert await client.async_devices() == []
        with pytest.raises(api.ProtocolError, match="ownership"):
            await client.async_status(MAC)
        with pytest.raises(api.ProtocolError, match="ownership"):
            await client.async_set_power(MAC, True)
        assert vendor.commands == []


@pytest.mark.asyncio
async def test_detail_owner_is_rechecked_after_previously_owned_status(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages[1][0].pop("userId")
        await client.async_status(MAC)
        vendor.detail["userId"] = 8
        with pytest.raises(api.ProtocolError, match="ownership"):
            await client.async_set_power(MAC, True)
        assert vendor.commands == []


@pytest.mark.asyncio
async def test_exact_zero_padded_cloud_number_maps_to_mac_and_is_preserved_for_controls(rsa_keys):
    cloud_number = "000000000000" + MAC.lower()
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages = [[{"id": 42, "deviceNum": cloud_number}]]
        vendor.detail["deviceNum"] = cloud_number
        result = await client.async_status(MAC)
        assert result["deviceNum"] == cloud_number
        assert (await client.async_devices())[0]["deviceNum"] == cloud_number
        await client.async_set_power(MAC, True)
        await client.async_set_humidity(MAC, 55)
        await client.async_purge(MAC)
        await client.async_set_locate(MAC, True)
        assert len(vendor.commands) == 4
        assert all(command["deviceNum"] == cloud_number and command["deviceId"] == 42 for command in vendor.commands)
        with pytest.raises(api.ProtocolError):
            await client.async_status(cloud_number)  # Config/API selection still requires a 12-digit MAC.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cloud_number",
    ["100000000000" + MAC, "00000000000" + MAC, "0000000000000" + MAC, "arbitrary-prefix" + MAC],
)
async def test_cloud_mapping_rejects_nonzero_or_inexact_prefix_before_detail_or_control(rsa_keys, cloud_number):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages = [[{"id": 42, "deviceNum": cloud_number}]]
        vendor.detail["deviceNum"] = cloud_number
        with pytest.raises(api.ProtocolError):
            await client.async_set_power(MAC, True)
        assert not any("getDeviceDetail" in path for _, path, _ in vendor.requests)
        assert vendor.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("detail_number", [MAC, "000000000000" + OTHER_MAC, "100000000000" + MAC])
async def test_cloud_detail_must_match_full_discovered_number_not_only_mac_suffix(rsa_keys, detail_number):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages = [[{"id": 42, "deviceNum": "000000000000" + MAC}]]
        vendor.detail["deviceNum"] = detail_number
        with pytest.raises(api.ProtocolError):
            await client.async_set_power(MAC, True)
        assert vendor.commands == []


@pytest.mark.asyncio
async def test_distinct_cloud_numbers_mapping_to_same_mac_are_rejected(rsa_keys):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages = [[{"id": 42, "deviceNum": MAC}, {"id": 42, "deviceNum": "000000000000" + MAC}]]
        with pytest.raises(api.ProtocolError, match="conflicting"):
            await client.async_set_power(MAC, True)
        assert vendor.commands == []


@pytest.mark.asyncio
async def test_live_detail_without_id_uses_validated_history_route_id_after_ownership_check(rsa_keys):
    cloud_number = "000000000000" + MAC.lower()
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages = [[{"id": 42, "deviceNum": cloud_number}]]
        vendor.detail.pop("id")
        vendor.detail["deviceNum"] = cloud_number
        result = await client.async_status(MAC)
        assert result["id"] == 42 and result["deviceNum"] == cloud_number
        assert (await client.async_devices())[0]["id"] == 42
        await client.async_set_power(MAC, True)
        assert vendor.commands == [{"deviceId": 42, "deviceNum": cloud_number, "frameType": "21", "data": "01"}]
        assert "id" not in vendor.detail  # The remote response is not mutated.


@pytest.mark.asyncio
@pytest.mark.parametrize("detail_id", [None, "", True, 43, "bad/id", {"id": 42}])
async def test_present_invalid_or_conflicting_detail_id_is_never_replaced(rsa_keys, detail_id):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages[1][0].pop("userId")
        vendor.detail["id"] = detail_id
        with pytest.raises(api.ProtocolError):
            await client.async_devices()
        with pytest.raises(api.ProtocolError):
            await client.async_set_power(MAC, True)
        assert vendor.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"userId": None}, {"userId": 8}, {"deviceNum": OTHER_MAC}])
async def test_missing_detail_id_does_not_bypass_full_identity_or_ownership(rsa_keys, change):
    async with client_vendor(rsa_keys) as (client, vendor):
        vendor.pages[1][0].pop("userId")
        vendor.detail.pop("id")
        vendor.detail.update(change)
        with pytest.raises(api.ProtocolError):
            await client.async_status(MAC)
        with pytest.raises(api.ProtocolError):
            await client.async_set_power(MAC, True)
        assert vendor.commands == []


class ExperimentalVendor(Vendor):
    def __init__(self):
        super().__init__()
        self.cloud_number = "000000000000" + MAC.lower()
        self.pages = [[{"id": 42, "deviceNum": self.cloud_number}]]
        self.detail.pop("id")
        self.detail.update(
            deviceNum=self.cloud_number,
            location="Basement",
            filter=[
                {
                    "id": 3,
                    "recommendUseHour": 1000,
                    "useHour": 900,
                    "extendUseHour": 0,
                    "token": TOKEN,
                    "wifiPassword": PASSWORD,
                }
            ],
        )
        self.read_data = {
            "iot/device/getDeviceHistoryDateList": [
                {
                    "lastTime": "2026-09-07 12:00:00",
                    "avgInTemp": 20,
                    "avgInHumidity": "55.5",
                    "token": TOKEN,
                    "deviceNum": self.cloud_number,
                    "ip": "192.0.2.20",
                }
            ],
            "iot/device/getDeviceHistoryTime": {"name": USERNAME, "useTime": "4.5", "password": PASSWORD},
            "iot/filter/getApiFilterDetail/3": {
                "id": 3,
                "discountRatio": 10,
                "discountCode": "FILTER10",
                "discountUrl": "http://192.0.2.20/private",
                "token": TOKEN,
            },
            "iot/device/checkDeviceNeedUpgrade/42": {"isUpgrade": False, "token": TOKEN},
            "iot/firmware/getUpgradeListByDevice/": {
                "totalRow": 1,
                "list": [
                    {
                        "firmwareVersion": "1.2.3.4",
                        "isUpgrade": 1,
                        "updateTime": "2026-09-07 12:00:00",
                        "deviceNum": self.cloud_number,
                        "password": PASSWORD,
                    }
                ],
            },
        }
        self.action_requests = []
        self.mutations = []
        self.sequence = []
        self.reject_path = None
        self.delay_path = None
        self.expire_path = None
        self.control_started = None
        self.control_release = None
        self.reset_used_hours = 0

    async def __call__(self, request):
        path = request.path.removeprefix("/rest/api/")
        if path == "iot/device/control":
            result = await super().__call__(request)
            self.sequence.append("control:" + str(self.commands[-1]["frameType"]))
            if self.control_started is not None:
                self.control_started.set()
                await self.control_release.wait()
            return result
        if path in self.read_data:
            self.action_requests.append((request.method, path, dict(request.query)))
            if path == self.expire_path and request.headers.get("token") == TOKEN:
                return response(code=10001)
            return response(self.read_data[path])
        if path in {
            "iot/device/updateDeviceName",
            "iot/device/updateDeviceLocation",
            "iot/device/updateFilterHour",
            "iot/device/resetFilter/42/3",
        }:
            body = await request.json()
            self.mutations.append((path, body))
            self.sequence.append(path)
            if path == self.reject_path:
                return response({"password": PASSWORD}, code=400)
            if path.endswith("updateDeviceName"):
                self.detail["name"] = body["name"]
            elif path.endswith("updateDeviceLocation"):
                self.detail["location"] = body["location"]
            elif path.endswith("updateFilterHour"):
                self.detail["filter"][0]["extendUseHour"] = 168
            else:
                self.detail["filter"][0]["useHour"] = self.reset_used_hours
            if path == self.delay_path:
                await asyncio.sleep(0.2)
            return response({"token": TOKEN})
        return await super().__call__(request)


@asynccontextmanager
async def experimental_client(rsa_keys, *, request_timeout=1):
    vendor = ExperimentalVendor()
    async with server(vendor) as url, aiohttp.ClientSession() as session:
        client = api.AlorairClient(
            session,
            USERNAME,
            PASSWORD,
            allow_insecure_http=True,
            base_url=url,
            public_key=rsa_keys[1],
            timeout=request_timeout,
        )
        yield client, vendor


@pytest.mark.asyncio
async def test_experimental_reads_use_owned_identity_exact_queries_and_sanitized_results(rsa_keys):
    async with experimental_client(rsa_keys) as (client, vendor):
        results = [
            await client.async_experimental_action(MAC, "history", period="month", query_date="2026-09-07"),
            await client.async_experimental_action(MAC, "operation_time", period="year", query_date="2026-09-07"),
            await client.async_experimental_action(MAC, "filters"),
            await client.async_experimental_action(MAC, "filter_detail", filter_id="3"),
            await client.async_experimental_action(MAC, "firmware_check"),
            await client.async_experimental_action(MAC, "firmware_history", page=2),
        ]
        assert results[0] == {
            "samples": [{"sample_time": "2026-09-07 12:00:00", "in_celsius": 20, "in_humidity": 55.5}]
        }
        assert results[1] == {"hours": 4.5}
        assert results[2]["filters"][0]["remaining_percent"] == 10
        assert results[2]["filters"][0]["remaining_days"] == 4.17
        assert results[3] == {"filter_id": "3", "discount_percent": 10, "discount_code": "FILTER10"}
        assert results[4] == {"update_available": False}
        assert results[5]["versions"][0]["version"] == "1.2.3.4"
        assert results[5]["page"] == 2
        assert vendor.action_requests[0][2] == {
            "deviceNum": vendor.cloud_number,
            "type": "2",
            "queryDate": "2026-09-07",
        }
        assert vendor.action_requests[1][2]["type"] == "3"
        assert vendor.action_requests[-1][2] == {"deviceId": "42", "pageNum": "2", "pageSize": "20"}
        for secret in (USERNAME, PASSWORD, TOKEN, MAC, vendor.cloud_number, "192.0.2.20"):
            assert secret not in json.dumps(results)
        assert vendor.commands == vendor.mutations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "parameters", "path", "body"),
    [
        ("rename", {"label": "Drying Room"}, "iot/device/updateDeviceName", {"id": 42, "name": "Drying Room"}),
        ("location", {"label": ""}, "iot/device/updateDeviceLocation", {"id": 42, "location": ""}),
        (
            "extend_filter",
            {"filter_id": 3, "extension": 2},
            "iot/device/updateFilterHour",
            {"id": 42, "filterId": "3", "extendUseHour": 2},
        ),
        ("reset_filter", {"filter_id": 3}, "iot/device/resetFilter/42/3", {}),
    ],
)
async def test_experimental_mutations_use_exact_payloads_and_observed_feedback(
    rsa_keys, action, parameters, path, body
):
    async with experimental_client(rsa_keys) as (client, vendor):
        result = await client.async_experimental_action(MAC, action, **parameters)
        assert result["accepted"] and result["confirmed"]
        assert vendor.mutations == [(path, body)]
        if action == "rename":
            assert vendor.commands == [
                {"deviceId": 42, "deviceNum": vendor.cloud_number, "frameType": "29", "data": "Drying Room"}
            ]
        else:
            assert vendor.commands == []
        for secret in (USERNAME, PASSWORD, TOKEN, MAC, vendor.cloud_number, "Drying Room"):
            assert secret not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("firmware_upgrade", {}),
        ("raw", {"frameType": 26}),
        ("history", {"period": [], "query_date": "2026-09-07"}),
        ("history", {"period": "day", "query_date": "2026-02-29"}),
        ("history", {"period": "day"}),
        ("firmware_history", {"page": 11}),
        ("firmware_history", {"page": True}),
        ("rename", {"label": " "}),
        ("rename", {"label": "x" * 65}),
        ("location", {"label": "a\nb"}),
        ("reset_filter", {"filter_id": "../other"}),
        ("extend_filter", {"filter_id": 3, "extension": True}),
        ("extend_filter", {"filter_id": 3, "extension": 168}),
        ("filters", {"endpoint": "user/cancelAccount"}),
    ],
)
async def test_invalid_experimental_requests_fail_before_network(rsa_keys, action, parameters):
    async with experimental_client(rsa_keys) as (client, vendor):
        with pytest.raises(api.AlorairError):
            await client.async_experimental_action(MAC, action, **parameters)
        assert vendor.requests == vendor.action_requests == vendor.mutations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("history", {"period": "day", "query_date": "2026-09-07"}),
        ("operation_time", {"period": "day", "query_date": "2026-09-07"}),
        ("filters", {}),
        ("filter_detail", {"filter_id": 3}),
        ("firmware_check", {}),
        ("firmware_history", {}),
        ("rename", {"label": "Room"}),
        ("location", {"label": "Room"}),
        ("extend_filter", {"filter_id": 3, "extension": 1}),
        ("reset_filter", {"filter_id": 3}),
    ],
)
async def test_every_experimental_action_requires_current_owned_detail(rsa_keys, action, parameters):
    async with experimental_client(rsa_keys) as (client, vendor):
        vendor.detail["userId"] = 8
        with pytest.raises(api.ProtocolError, match="ownership"):
            await client.async_experimental_action(MAC, action, **parameters)
        assert vendor.commands == vendor.action_requests == vendor.mutations == []


@pytest.mark.asyncio
async def test_filter_identity_and_remaining_life_gate_block_mutation(rsa_keys):
    async with experimental_client(rsa_keys) as (client, vendor):
        for action in ("filter_detail", "reset_filter", "extend_filter"):
            parameters = {"filter_id": 4, **({"extension": 1} if action == "extend_filter" else {})}
            with pytest.raises(api.ProtocolError, match="does not belong"):
                await client.async_experimental_action(MAC, action, **parameters)
        vendor.detail["filter"][0]["useHour"] = 800
        with pytest.raises(api.CommandError, match="below 20"):
            await client.async_experimental_action(MAC, "extend_filter", filter_id=3, extension=1)
        assert vendor.action_requests == vendor.mutations == []


@pytest.mark.asyncio
async def test_experimental_read_authentication_refresh_is_bounded(rsa_keys):
    async with experimental_client(rsa_keys) as (client, vendor):
        vendor.expire_path = "iot/device/checkDeviceNeedUpgrade/42"
        assert await client.async_experimental_action(MAC, "firmware_check") == {"update_available": False}
        assert vendor.logins == 2 and len(vendor.action_requests) == 2
        assert vendor.commands == vendor.mutations == []


@pytest.mark.asyncio
async def test_rename_partial_rejection_is_uncertain_and_never_replays_first_command(rsa_keys):
    async with experimental_client(rsa_keys) as (client, vendor):
        vendor.reject_path = "iot/device/updateDeviceName"
        with pytest.raises(api.CommandError) as caught:
            await client.async_experimental_action(MAC, "rename", label="PRIVATE_LABEL")
        assert caught.value.outcome_unknown and "partially" in str(caught.value)
        assert len(vendor.commands) == len(vendor.mutations) == 1
        assert vendor.logins == 1
        for secret in (PASSWORD, TOKEN, "PRIVATE_LABEL"):
            assert secret not in str(caught.value)


@pytest.mark.asyncio
async def test_location_timeout_is_unknown_and_not_replayed(rsa_keys):
    async with experimental_client(rsa_keys, request_timeout=0.05) as (client, vendor):
        vendor.delay_path = "iot/device/updateDeviceLocation"
        with pytest.raises(api.CommandError) as caught:
            await client.async_experimental_action(MAC, "location", label="Room")
        assert caught.value.outcome_unknown
        assert len(vendor.mutations) == 1
        assert vendor.logins == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("before", "after"), [(900, 901), (900, 900), (0, 0)])
async def test_reset_filter_requires_observed_reset_to_full_after_positive_baseline(rsa_keys, before, after):
    async with experimental_client(rsa_keys) as (client, vendor):
        vendor.detail["filter"][0]["useHour"] = before
        vendor.reset_used_hours = after
        result = await client.async_experimental_action(MAC, "reset_filter", filter_id=3)
        assert result["accepted"] is True
        assert result["confirmed"] is False
        assert result["filter"]["used_hours"] == after
        assert vendor.mutations == [("iot/device/resetFilter/42/3", {})]


@pytest.mark.asyncio
async def test_rename_transaction_cannot_be_interleaved_with_power_command(rsa_keys):
    async with experimental_client(rsa_keys) as (client, vendor):
        vendor.control_started, vendor.control_release = asyncio.Event(), asyncio.Event()
        rename = asyncio.create_task(client.async_experimental_action(MAC, "rename", label="Room"))
        await vendor.control_started.wait()
        power = asyncio.create_task(client.async_set_power(MAC, False))
        try:
            await asyncio.sleep(0)
            assert vendor.sequence == ["control:29"]
        finally:
            vendor.control_release.set()
            await asyncio.gather(rename, power)
        assert vendor.sequence == ["control:29", "iot/device/updateDeviceName", "control:21"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "parameters", "path", "data"),
    [
        ("history", {"period": "day", "query_date": "2026-09-07"}, "iot/device/getDeviceHistoryDateList", [{}]),
        (
            "history",
            {"period": "day", "query_date": "2026-09-07"},
            "iot/device/getDeviceHistoryDateList",
            [{}] * (api.MAX_HISTORY_SAMPLES + 1),
        ),
        (
            "operation_time",
            {"period": "day", "query_date": "2026-09-07"},
            "iot/device/getDeviceHistoryTime",
            {"useTime": PASSWORD},
        ),
        ("filter_detail", {"filter_id": 3}, "iot/filter/getApiFilterDetail/3", {"id": 4, "discountRatio": 10}),
        ("filter_detail", {"filter_id": 3}, "iot/filter/getApiFilterDetail/3", {"discountCode": PASSWORD}),
        ("firmware_check", {}, "iot/device/checkDeviceNeedUpgrade/42", {"isUpgrade": "maybe"}),
        ("firmware_history", {}, "iot/firmware/getUpgradeListByDevice/", {"totalRow": 21, "list": [{}] * 21}),
    ],
)
async def test_unsupported_experimental_response_shapes_fail_without_private_data(
    rsa_keys, action, parameters, path, data
):
    async with experimental_client(rsa_keys) as (client, vendor):
        vendor.read_data[path] = data
        with pytest.raises(api.ProtocolError) as caught:
            await client.async_experimental_action(MAC, action, **parameters)
        assert PASSWORD not in str(caught.value)
        assert vendor.commands == vendor.mutations == []
