# Reconstructed AlorAir-Lite protocol

This documents independently written interoperability code based on static analysis of **AlorAir-Lite Android 2.0.8**, package `com.ruifeng.alorairrliteNew`, and device-scoped native TCP observations. The vendor does not publish this as a supported API contract. App binaries, extracted source and raw device captures are not distributed in this repository. The cloud contract and [experimental local contract](#experimental-local-tcp-transport) have different evidence and entity surfaces; cloud fields must not be treated as validated native offsets. See [cloud validation](LIVE_VALIDATION.md) and [local evidence limits](LOCAL_CONTROL.md#available-controls-and-evidence).

The Storm Pro/Lite contract must not be substituted with Sentinel/AlorAir-C protocol constants. The manufacturer's [old/new app comparison](https://www.alorair.com/blog/spotting-the-difference-old-vs-new-alorair-apps/) describes controller-generation differences.

## Cloud transport and authentication

The app's REST base is `http://online-app1.toovem.com:8081/rest/api/`. The tested endpoint requires HTTP; an interchangeable HTTPS endpoint has not been established. See [Security](SECURITY.md).

Requests include `env: app`, `platform: 1`, `lang: en`, `App-Identity: 1` and `Accept: application/json`. POST bodies are JSON. Authenticated calls add a `token` header.

| Request | Body/query | Purpose |
| --- | --- | --- |
| `POST user/login` | `username`, RSA-encrypted/Base64 `password`, `method: 1` | Establish session. Password encoding uses the app's public RSA key and PKCS#1 v1.5; it does not provide TLS security. |
| `GET user/getUserInfo` | None | Obtain account identity for ownership checks. |
| `GET iot/device/getHistoryDeviceList` | `pageNum`, `pageSize` | Enumerate authenticated historical/owned devices. The integration checks ownership rather than trusting every list entry. |
| `GET iot/device/getDeviceDetail/{deviceId}` | None | Read one identity-checked device. |
| `POST iot/device/control` | Control fields below | Send a device command. |

The generic success envelope has HTTP status 200 and JSON `code: 200`, with the result in `data`. The app treats codes at or above 10000 as invalid authentication. There is no documented command-specific physical ACK schema.

Read requests have bounded authentication refresh. A timeout, malformed response or other uncertain command result must not trigger blind replay: a command may already have reached the appliance. Redirects are refused so account credentials/tokens are not forwarded to another location.

The cloud's opaque `deviceId`, cloud `deviceNum` and Wi-Fi MAC are distinct. Live evidence shows a 24-character `deviceNum`: exactly twelve zeros followed by the twelve hexadecimal MAC digits. Setup still takes the ordinary Wi-Fi MAC. The client accepts only this exact padded shape or a full conventional MAC, retains the vendor's original cloud number for commands, and rejects arbitrary suffix matches. History rows can omit `userId`, so they are discovery candidates only: detail must match the complete cloud number and prove `userId == authenticated uid` before status or control. The validated history ID anchors the detail request URL. Live detail omits that internal ID; if an ID is present it must match, and only a missing ID is supplied from the request after identity and ownership pass. It does not call the app's `switchDevice` endpoint while polling.

## Cloud command contract

In this table, `D` is the authenticated opaque device ID and `N` is that same device's full device number. These are symbolic values, never guessed IDs. Every row uses `POST iot/device/control`.

| Action | JSON fields | Meaning and app constraints |
| --- | --- | --- |
| On/off | `deviceId: D`, `deviceNum: N`, `frameType: "21"`, `data: "01"` / `"00"` | Native power request. `powerStatus` 01 and 02 both mean enabled. |
| Start purge | `deviceId: D`, `deviceNum: N`, `frameType: "22"`, `data: "01"` | App requires on, no active fault and no current purge. No supported cancel-purge path was found. |
| Humidity target | `deviceId: D`, `deviceNum: N`, `frameType: "23"`, `data: "50"` | **Decimal string** target. UI offers 25–80% in 5% increments; requires on. |
| Continuous | Same target command, `data: "20"` | 20 is the app's CO sentinel, not a numeric 20% target. |
| Locate | `deviceId: D`, `deviceNum: N`, `frameType: "27"`, `data: "01"` / `"00"` | Locate on/off. Main app view requires on; other app views differ, so off-state behavior needs verification. |
| Temperature display unit | `deviceId: D`, `frameType: 24`, `data: "00"` / `"01"` | Celsius/Fahrenheit. **Omit `deviceNum`; frameType is a JSON number.** |
| Specific-humidity display unit | `deviceId: D`, `frameType: 25`, `data: "00"` / `"01"` | Grains/lb or g/kg. **Omit `deviceNum`; frameType is a JSON number.** |

The app allows unit changes while off. Its power UI rejects faults other than raw 00/20; this integration preserves the ability to request a normal off for an available faulted unit. An accepted control response is followed by state polling; it does not overwrite telemetry with the requested state.

## Cloud state interpretation

| Vendor fields | Interpretation |
| --- | --- |
| `powerStatus` | Hex string 00/off, 01/enabled, 02/enabled and target reached/idle. There is no independently measured compressor-state field. |
| `currentHumidity` | **Target** RH; 20 means continuous. |
| `inHumidity`, `outHumidity` | Measured intake/outlet RH, percent. |
| `inCelsius`, `outCelsius` | Intake/outlet temperature in °C. |
| `inFahrenheit`, `outFahrenheit` | Separate intake/outlet temperature in °F. |
| `inGkg`, `outGkg` | Intake/outlet specific humidity, g/kg. |
| `inGrlb`, `outGrlb` | Intake/outlet grains/lb. |
| `temperatureUnit` | 0/Celsius, 1/Fahrenheit. |
| `humidityUnit` | 0/grains per lb, 1/g per kg; this is not an RH unit switch. |
| `drainStatus`, `locateFunction`, `defrostingStatus` | 01 indicates active; valid 00 indicates inactive. Missing is unknown. |
| `errCode` | Hex bitmask below. |
| `updateTimeStr` | Vendor sample timestamp; distinct from local cloud-fetch time. |
| `singleWorktime` | App displays “Total Working Time”, hours. Session-versus-lifetime reset behavior is unproven. |
| `coilTemperature` | Coil temperature with an app suffix following the selected temperature unit; do not assume always Celsius without verification. |
| `cfm`, `pints` | App/model airflow and capacity values. They do not establish measured fan speed or collected water volume. |

Only fields actually returned and accepted by the integration should produce values. Preserve zero; do not turn an absent field into zero. Metadata such as product/name/firmware may vary by server and model. Raw data may contain account, location and network details, so responses are allowlisted rather than copied wholesale into entities or diagnostics.

The app derives its “dehumidifying” display from power status and humidity heuristics. It is not feedback from a compressor relay. Likewise, the static fan/timer markup has no working control contract.

### Faults

Parse strings as hexadecimal: `"10"` means 0x10. More than one bit can be set.

| Bit mask | Display code | Established meaning |
| --- | --- | --- |
| 0x01 | E1 | Fault; component diagnosis is not established here. |
| 0x02 | L0 | Low temperature. |
| 0x04 | HI | High temperature. |
| 0x08 | E5 | App translation key identifies refrigerant-leak alert. |
| 0x10 | E4 | Fault; component diagnosis is not established here. |
| 0x20 | None | Ignored by the app's fault display; meaning unknown, not an asserted fault. |
| 0x40 | E3 | Fault; component diagnosis is not established here. |
| 0x80 | E7 | Fault; component diagnosis is not established here. |

Consult the unit's model-specific documentation for action on a fault. Do not transfer error-code diagnoses from another product family.

### Freshness and pending commands

The app references no explicit online boolean. Its device list shows an unknown/offline-style indicator when `powerStatus` is missing; the string `"00"` is truthy and specifically means off. That UI does not establish whether a retained cloud record is current.

For an ordinary timezone-less `updateTimeStr`, the app's formatter effectively interprets the value as UTC and displays local time. This is a strong static inference, not a verified server timezone contract. It also uses the client's current timezone offset, which is unsuitable for historical timestamps across daylight-saving boundaries. A live enabled-unit observation confirmed UTC alignment within ten seconds across four reads and advancing samples over one minute. Subsequent live on/off commissioning confirmed advancing samples approximately every 30 seconds in both states, including while the restart guard counted down. This observation does not establish every firmware or server timezone contract.

The integration records when cloud status was fetched separately from the vendor sample time. The initial guard treats missing/unparseable timestamps, samples more than 300 seconds old, and timestamps more than 60 seconds in the future as stale. Naive timestamps are interpreted as UTC, based on the app formatter. This makes ordinary device entities unavailable and rejects commands while status is stale; the sample-time/freshness diagnostics remain inspectable when the cloud poll itself succeeded. A refresh requests another cloud read; it cannot force a new physical sample.

An unchanged cached sample does not become fresh merely because another HTTP request succeeded. The five-minute limit is provisional, not a verified firmware reporting specification. Its behavior, including whether a normally powered-off unit stops reporting, needs validation against real reporting intervals before unattended operation is considered proven.

Pending commands represent intent awaiting observed state. They are not entity-state updates or physical confirmations. Their current expiration is 300 seconds. An expired pending marker does not prove that a command succeeded or failed. Inspect resulting telemetry and sample times; avoid rapidly repeating uncertain power or purge commands. The integration enforces a minimum 180-second off interval after observing a stop, and starts a conservative interval after loading/reloading.

The last observed numeric target is retained during a running integration session and recorded in Home Assistant's saved entity state. When starting in continuous mode, the integration restores a valid saved target if available. Returning to `auto` uses that target, or 50% if no numeric target or valid saved history is available. An explicit target command avoids that fallback. Restoring this setting does not issue a power or humidity command by itself.

## Cloud implementation boundaries

The app also contains cloud name/location changes, filter-maintenance and OTA APIs. They are outside this integration's initial command surface; installation and polling do not rename, rebind, reset filters, change sharing or trigger firmware updates.

No reachable fan speed, fan run-on, timer/schedule or sensor-calibration command was found. Scheduling and external humidity control belong in [Home Assistant automations](https://www.home-assistant.io/docs/automation/). Unused BLE helpers do not establish a local Storm Pro control interface, and the vendor's WebSocket endpoint is not used by this polling implementation.

Command confirmation requires a sample newer than the pre-command baseline and not before the command’s second of issue. Pending off blocks on until confirmation; the off dwell is restarted conservatively on an off request and on confirmed off.

Each command has a 120-second total deadline covering preflight status, discovery, authentication, the control request and feedback. Individual API requests also have a 20-second timeout, read authentication refresh is limited to one retry, and history is limited to 100 pages. The total deadline prevents those individual bounds from multiplying into a prolonged unload. Deadline expiry cancels and joins the client coroutine, retains uncertain command intent and never replays the command.

Unload blocks new commands and drains submitted command tasks before unloading the platforms. Cancellation of the unload caller is consumed until that critical cleanup finishes, so it cannot permit a replacement coordinator to overtake an old local command. A pending command does not cause unload to return `False`: Home Assistant 2026.6.2 treats that as nonrecoverable `FAILED_UNLOAD`, not a retryable deferral. An HTTP timeout cannot retract a command already accepted by the vendor or prove its physical outcome; subsequent device feedback and live commissioning remain necessary.

## Experimental service extensions

The opt-in `experimental_action` service uses the same authenticated, exact-device ownership checks. It adds bounded history, operating-time, attached-filter and firmware-information reads, plus explicit vendor-label and filter-maintenance writes. Read actions never install firmware. Rename uses frame `"29"` followed by `iot/device/updateDeviceName`; partial delivery is not replayed. Filter-reminder extension is a vendor enum (week/month/three months), not a raw hour count. See [Experimental actions](EXPERIMENTAL.md) for the supported interface and feedback limits.

The Last command diagnostic keeps request acknowledgement separate from a fresh matching device sample. Confirmation requires a sample timestamp at least as recent as the full request timestamp and newer than the baseline. A coarse timestamp within the same second cannot prove ordering; confirmation waits for a later report. Transient draining feedback is retained after the draining flag clears, until another command or a reload replaces the diagnostic. A reported flag still does not independently measure water flow or a relay.

## Local connectivity research

The [manufacturer's Lite setup guidance](https://www.alorair.com/alorair-lite-wifi-app) and analyzed application establish BLE-assisted Wi-Fi provisioning. The reachable application path scans for `bk7231`, uses service FFF0, notifications FFF1 and writes FFF2 (Bluetooth base UUID). It writes Wi-Fi credentials and then updates the authenticated cloud binding. This is a configuration flow; it does not establish BLE operating controls after provisioning.

Unused helper definitions mention identity reads and MQTT configuration, but no operating-screen callers or verified firmware support were found. The app's nearby-device list is a cloud request keyed by Wi-Fi identifiers, rather than a demonstrated local discovery/control protocol. No device reset, provisioning replay or MQTT reconfiguration is needed to inspect these facts.

A separate maintainer's [local TCP bridge](https://github.com/sethrobin/alorair-local/tree/d056eaa792fb70e1a5c097be87da1bde16080afc) reports a working Sentinel HDi65S / AlorAir-C implementation using the device's outbound TCP connection to port 6200. Several command numbers and the reserved/MAC envelope structure resemble the Lite contract, making this a useful interoperability lead. That controller's continuous-mode value and state interpretation differ. The [wire protocol](https://github.com/sethrobin/alorair-local/blob/d056eaa792fb70e1a5c097be87da1bde16080afc/PROTOCOL.md) must not be assumed valid for a Storm/Lite unit without its own capture and validation.

Subsequent Storm/Lite captures established the native contract below, and a standalone local endpoint confirmed power ON/OFF, targets 50/55, continuous-mode restoration, display changes and one warm reconnect. Temporary Home Assistant commissioning later confirmed display changes, ON/OFF and targets 50/55, but its continuous-mode request failed to confirm. These observations do not validate every upstream field or command, a BLE control path, reliable unattended operation or a WAN-independent Home Assistant installation.

## Experimental local TCP transport

The experimental profile listens for an inbound appliance connection. One Lite-equipped Storm Pro was observed initiating TCP to port **6100**. The integration does not connect outward to the device or vendor, provision Wi-Fi, or configure router rules. The network must explicitly direct that device's connection to the listener; see [Local setup](LOCAL_CONTROL.md#network-requirements).

### Frame structure

Offsets below are zero-based. All multibyte integers are big-endian.

| Offset | Length | Meaning |
| --- | --- | --- |
| 0 | 2 | Start marker `0D 0E` |
| 2 | 6 | Reserved zero bytes |
| 8 | 6 | Full device Wi-Fi MAC |
| 14 | 4 | Timestamp token; observed device-originated frames used zero |
| 18 | 2 | Data length, `N` |
| 20 | 1 | Function |
| 21 | 1 | Opcode |
| 22 | 4 | Reserved zero bytes |
| 26 | `N` | Data |
| `26 + N` | 2 | Additive checksum: `sum(frame[:-3]) & 0xffff` |
| `28 + N` | 1 | Terminator `16` |

The complete frame length is **`29 + N`**. The checksum's high byte is not a message class or model marker. A length value such as `0x0022` means 34 data bytes; it does not identify a product family. Parsing is length-delimited after TCP reassembly, with strict identity, header, length and full-checksum checks. The four body-reserved bytes are retained; the recognized heartbeat specifically requires them to be zero. Neither the MAC nor the checksum authenticates a sender cryptographically.

### Observed functions and commands

| Direction / purpose | Function / opcode | Data | Evidence |
| --- | --- | --- | --- |
| Device heartbeat | `09 / 01` | Empty | Captured and handled by the local endpoint. |
| Endpoint heartbeat reply | `09 / 01` | One byte `00` | Repeated local exchanges succeeded. |
| Temperature display selection | `09 / 24` | `00` Celsius, `01` Fahrenheit | Both changes and restoration were confirmed through local device reports. |
| Baseline device status | `07 / 1C` | 34 bytes on the tested unit | Observed periodically while off and across warm reconnection. |
| Display command report | `07 / 24` | 34 bytes | Data offset 32 changes between `00`/`01`; full-frame offset 58. |
| Power request | `09 / 21` | `01` on, `00` off | Captured from the vendor connection and subsequently confirmed through standalone and Home Assistant local ON/OFF trials. |
| Power command report | `07 / 21` | 34 bytes | Data offset 3 (full-frame offset 29) changed between `01`/`00` after on/off requests, including local commissioning. |
| Target/continuous request | `09 / 23` | One binary byte: 50=`0x32`, 55=`0x37`, continuous 20=`0x14` | Standalone 50 → 55 → 20 succeeded. Home Assistant confirmed 50/55, but a valid `0x14` request received no matching reply and reports retained 55. Other numeric targets are untested locally; reliability remains unresolved. |
| Target command report | `07 / 23` | 34 bytes | Target at data offset 23 (full-frame offset 49). The captured 55 → 50 echo changed only that data byte. |

Only the observed native power values `00` and `01` map to off/on. Other values are unknown; the cloud interpretation of `powerStatus=02` is not transferred into the native decoder. The reports establish device-reported enabled state, not compressor or pump current. Another field changed during shutdown, but its physical meaning remains unmapped.

The native target command carries one **binary** byte, unlike the cloud API's decimal-string value. The decoder accepts 20 (continuous) or 25–80 in steps of five; other reported values remain unknown. This is a target, not measured intake RH. The HA dehumidifier maps it to target and auto/continuous controls, and takes its current humidity from the separately decoded inlet measurement below rather than from this byte. Native faults, purge, locator and specific-humidity display settings remain unmapped. Similar opcode numbers in another controller's project do not validate those fields.

### Status data layout (34 bytes)

Offsets below are into the 34-byte data field of a `07` status frame and are zero-based; add 26 for the full-frame offset. Multibyte values are big-endian.

| Data offset | Length | Meaning |
| --- | --- | --- |
| 3 | 1 | Power: `00` off, `01` on. Other values are unknown. |
| 8 | 1 | Inlet temperature, °C |
| 9 | 1 | Inlet temperature, °F |
| 10 | 1 | Inlet relative humidity, percent |
| 11 | 1 | Outlet temperature, °C |
| 12 | 1 | Outlet temperature, °F |
| 13 | 1 | Outlet relative humidity, percent |
| 14 | 2 | Inlet grains per pound |
| 16 | 2 | Inlet specific humidity, g/kg |
| 18 | 2 | Outlet grains per pound |
| 20 | 2 | Outlet specific humidity, g/kg |
| 23 | 1 | Humidity target; 20 selects continuous mode |
| 32 | 1 | Temperature display selection: `00` Celsius, `01` Fahrenheit |

The measurement block at offsets 8–21 was reverse-engineered from packet captures of one unit's native status reports and cross-checked against the vendor cloud's readings of the same sensors. The Fahrenheit and Celsius bytes agree with each other, and the grains and g/kg fields are consistent with each other and with the paired temperature and humidity. The values also move as expected while the unit runs.

The decoder treats the temperature bytes as signed and reports a temperature only when its paired Fahrenheit byte confirms the conversion. Humidity is reported only within 0–100%. Any other value is left unknown rather than guessed. Sub-zero temperatures follow from the signed interpretation and were not observed.

The remaining data bytes are **candidates, not established fields**, and none is mapped to an entity:

- Offset 0 is a constant `0x20`, a candidate for the cloud fault mask's always-ignored `0x20` bit.
- Offset 22 falls during operation and recovers afterwards, a coil-temperature candidate with no cloud cross-reference.
- Offsets 24–25 and 28–29 slowly increment, so both are counter candidates.
- Offsets 4, 6 and 7 are 0/1 flags that change around power transitions.

Purge, drain, locate, defrost and fault events never occurred in the captures, so those states remain unmapped. The absence of a local fault entity is not evidence that the unit is fault-free.

### Freshness, command matching and cancellation

Local sample time is Home Assistant's receipt time. The client also tracks a monotonic receipt time, connection/session identity and receive sequence; it does not invent a device timestamp from the observed zero token. A fresh TCP connection does not reuse the prior session's sample.

Commands are serialized and spaced at least 1.1 seconds after the preceding outbound frame, with a later timestamp token. A paced local display trial succeeded after an earlier closely spaced command was ignored. Both spacing and timestamp separation changed, so the cause and minimum firmware timing requirement remain unproven.

A command result requires a matching opcode/value on the same connection, after its write boundary. That boundary includes all bytes already delivered to the stream reader, including complete unread frames and partial parser-buffer frames. A buffered pre-command report cannot confirm the new request. Heartbeat traffic and unrelated status opcodes do not confirm an operating command.

Timeout or disconnection after queueing is reported as uncertain. The client never automatically replays that command on reconnection. Caller cancellation cancels and joins a pending send before returning, so a delayed ON cannot be transmitted behind a subsequent OFF. Cleanup closes accepted sockets, drains owned tasks and releases the listener. These software checks are covered with synthetic socket tests; they do not establish physical shutdown during an appliance/network failure.

The Home Assistant local coordinator retains uncertain intent for the original 300-second pending window. An identical request during that window raises `ServiceValidationError` explaining the uncertain previous delivery, that no retry was sent, and the need to wait for expiry and inspect fresh status. It does not silently return service success or extend suppression. Concurrent duplicates wait behind the original command: a confirmed original can leave the duplicate as an already-satisfied no-op, while an uncertain original makes the duplicate fail explicitly without a second native call. Expiry alone does not establish the earlier command's outcome.

Fresh status is required before starting, changing display units or setting humidity. Ordinary HA target/mode changes also require reported ON, checked again at the native write boundary after pacing. An OFF report arriving during pacing rejects the target before bytes are sent. A safety OFF request is permitted on a verified connected session even if its status has become stale, but still needs a matching later report for confirmation. The client also has an explicit stale-status override for bounded restoration of a previously known humidity target; ordinary HA actions do not use that override. Identity, connection and later-report checks remain mandatory. The Home Assistant power option gates ON from both local power controls and is disabled by default.

### Validation boundary

A standalone endpoint completed local Fahrenheit → Celsius → Fahrenheit and one deliberate warm disconnect/reconnect, with fresh status/heartbeat afterward. A later standalone trial confirmed native ON and OFF. Scoped rollback rules were removed cleanly, and fresh cloud OFF state was verified in Home Assistant after recovery. A subsequent standalone local trial completed ON → 50 → 55 → 20 → OFF with matching same-session reports and continuous mode restored. Independent capture analysis verified the matching commands, three-minute OFF interval, scoped rollback and fresh cloud OFF reports afterward.

Integration 0.3.0 was then temporarily commissioned through actual Home Assistant entities and services. The local dehumidifier retained its registered identity; Fahrenheit → Celsius → Fahrenheit, ON and targets 50/55 received matching reports. A valid native continuous request (`09/23`, data `0x14`) was sent, but no matching `07/23` reply followed and subsequent status retained target 55. The action timed out; a later OFF command was confirmed with target 55 still selected. The scoped network guard rolled back cleanly. This is partial commissioning with an unresolved command-reliability finding, not successful continuous-mode restoration or production readiness. No firmware cause has been established. Cold boot, persistent offline DNS, future destination changes and sustained offline operation remain unverified. See [Validation](LIVE_VALIDATION.md#experimental-local-commissioning).
