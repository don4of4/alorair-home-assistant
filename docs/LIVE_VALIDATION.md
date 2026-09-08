# Validation

The integration is tested against Home Assistant 2026.6.2 using its actual config-flow, entity, service and reload machinery. Client tests use controlled HTTP and TCP servers to verify authentication, exact-device ownership, request formats, bounded failures and uncertain delivery without replay.

## Hardware coverage

One Storm Pro with an AlorAir-Lite controller has been connected successfully. Cloud metadata labels that device Storm Ultra New, illustrating why app/controller identity is more reliable than a catalog name alone. See [Compatibility](COMPATIBILITY.md) for manufacturer-listed models and the limits of that evidence.

Fresh cloud reports confirmed native power, relative-humidity target changes, continuous/auto mode, purge, locator and display-unit changes. Native config-entry reload recovered current state, retained off state and reapplied restart protection. These observations are device-reported cloud feedback, not independent measurements of electrical load, compressor operation or water flow.

A successful HTTP response alone is not actuation proof. A fresh matching device report is stronger evidence; physical behavior should be checked during commissioning. The draining flag cannot prove that water reaches a drain.

## Version 0.2 commissioning

During version 0.2 commissioning, one requested purge produced a fresh draining-on sample, followed by draining-off. **Last command** retained `device_reported` after draining cleared. Normal power-off was subsequently confirmed by a fresh sample; no fault was reported. Locator on/off and power feedback were also checked. This establishes the feedback path on the tested controller, not pump flow or indicator visibility.

The experimental service was enabled through the native options flow and invoked through Home Assistant's service API:

| Action | Live result |
| --- | --- |
| `filters` | Returned an attached filter and bounded life counters. |
| `filter_detail` | Returned replacement-discount metadata for that attached filter. |
| `firmware_check` | Returned no update available. No installation was requested. |
| `firmware_history` | Returned a valid empty page. Nonempty pages remain fixture-tested only. |
| `history`, `operation_time` | Timed out under the integration's 20-second request limit. A separate read-only diagnostic using the app's 30-second allowance received vendor application code 500 after about 30 seconds for both endpoints. No live history data was verified. |
| `rename`, `location`, `extend_filter`, `reset_filter` | Implemented and covered by controlled-request tests; not invoked on hardware during commissioning. |

The failed history requests match the app's endpoint and parameter formats. They are not evidence that the service works reliably, and an empty result is not substituted for a failed request. Read failures leave the preceding device-command diagnostic intact. Cloud behavior can change after this observation.

## Experimental local commissioning

The following evidence was established on the same already-provisioned Lite controller. Standalone endpoint trials, cloud-originated wire captures temporary Home Assistant trials and a persistent local installation provide distinct evidence. Integration version 0.3.0 was partially commissioned through actual Home Assistant entities and services, but a continuous-mode command failed to confirm. The local profile remains experimental and is not ready for unattended production use.

| Operation | Evidence | Remaining limit |
| --- | --- | --- |
| Local power ON/OFF | A standalone local endpoint observed the required OFF interval, requested ON once and then OFF, and received matching native reports for both. | Enabled-state telemetry does not measure compressor operation or drying output. |
| Local temperature display | Standalone and Home Assistant Fahrenheit → Celsius → Fahrenheit trials completed with matching native reports. Home Assistant reported `device_reported` for both changes. | This changes display units, not the measured temperature. |
| Warm TCP reconnect | After a deliberate connection close, the device opened a fresh local connection and reported status/heartbeat. | Cold boot and sustained reconnection behavior remain untested. |
| Power-trial rollback | Temporary scoped routing rules were removed cleanly, and Home Assistant subsequently showed fresh cloud OFF state. | Network recovery does not guarantee a shutdown command during an outage. |
| Native humidity target/continuous mapping | Cloud-originated native commands established 50 → 55 → 50 → 20. Opcode `09/23` carries one binary byte; matching `07/23` reports carry the target at data offset 23. The 55 → 50 echo changed only that data byte. | The mapping describes target selection, not measured humidity. |
| Standalone local humidity trial | ON → 50 → 55 → 20 → OFF completed with matching same-session power/target reports. Continuous mode was restored and the runner reported no evidence-write failure. | Only numeric targets 50%/55% and continuous 20 were exercised. Independent capture analysis verified all five matching commands, the three-minute OFF interval, scoped rule removal and fresh cloud OFF reports after rollback. |
| Home Assistant local installation | A temporary integration 0.3.0 installation received fresh native reports and retained the registered dehumidifier entity ID when reconfigured from cloud. Six entities include target and auto/continuous controls. Synthetic sockets additionally exercise real HA setup, services, reconfiguration and cleanup. | Intake humidity remains unknown; native fault, purge and locator controls are absent. A working installation does not establish reliable unattended control. |
| Home Assistant power and numeric targets | An explicit dehumidifier ON action and targets 50%/55% received matching device reports. A later OFF action also confirmed. | These observations establish reported state, not compressor operation or drying performance. Other numeric targets remain untested locally. |
| Home Assistant continuous-mode failure | The continuous action sent a valid native `09/23` request with one-byte data `0x14` (20), but no matching `07/23` reply followed. The action timed out and subsequent reports retained target 55. OFF was then confirmed with target 55 still selected. | This differs from the successful standalone continuous-mode trial. The cause remains unresolved; neither firmware behavior nor a specific timing defect has been established. The failure must not be reported as successful continuous-mode restoration. |
| Home Assistant trial rollback | The device-scoped network guard removed its temporary rules cleanly after confirmed OFF. The cloud profile was restored, and fresh cloud reports confirmed recovery and subsequent continuous-mode restoration. | A later cloud OFF request also failed to confirm; a deliberate request after the pending window expired received matching OFF feedback. Neither transport guarantees delivery. |
| Persistent local installation | Managed DNS, a reserved device address, narrow router NAT/firewall rules and the existing Home Assistant local listener were installed. With a MAC-scoped IPv4/IPv6 WAN block enabled, one ON and one OFF action received fresh matching local reports, with continuous mode retained and no retries. No mode/target change or physical power cycle was used for this final check. | Device-reported power and mode do not independently measure compressor operation or water flow. Cold boot, router/NAS restart recovery and long-running operation were not tested. |

Repeated local command reliability, cold boot, long-running offline drying, recovery of the installed DNS/routing after restarts, future destination changes and other model/controller revisions remain unverified. The valid continuous request with no confirming state change is an open reliability finding, even though the standalone trial succeeded. Private captures and network details are excluded from this repository; the public protocol and tests use generic field descriptions and synthetic identities. See [Local control](LOCAL_CONTROL.md) for setup requirements and [Protocol](PROTOCOL.md#experimental-local-tcp-transport) for the decoding and acknowledgement rules.

## Contributor checks

Run `make check` to execute Ruff and the integration tests. Temporary Home Assistant instances and HTTP/TCP servers are stopped by the tests. Use sanitized fixtures; never commit account credentials, device/network identifiers, raw packet captures or extracted vendor application code.

When validating a new model, record its exact label/controller generation, app name/version, firmware if known, Home Assistant version, tested integration revision and which individual operations returned fresh feedback. Distinguish supported, experimentally observed and untested operations. Keep installation-specific schedules and room details outside the integration repository.
