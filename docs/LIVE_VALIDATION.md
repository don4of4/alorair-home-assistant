# Validation

The integration is tested against Home Assistant 2026.6.2 using its actual config-flow, entity, service and reload machinery. Client tests use controlled HTTP servers to verify authentication, exact-device ownership, request formats, bounded failures and uncertain delivery without replay.

## Hardware coverage

One Storm Pro with an AlorAir-Lite controller has been connected successfully. Cloud metadata labels that device Storm Ultra New, illustrating why app/controller identity is more reliable than a catalog name alone. See [Compatibility](COMPATIBILITY.md) for manufacturer-listed models and the limits of that evidence.

Fresh cloud reports confirmed native power, relative-humidity target changes, continuous/auto mode, purge, locator and display-unit changes. Native config-entry reload recovered current state, retained off state and reapplied restart protection. These observations are device-reported cloud feedback, not independent measurements of electrical load, compressor operation or water flow.

A successful HTTP response alone is not actuation proof. A fresh matching device report is stronger evidence; physical behavior should be checked during commissioning. The draining flag cannot prove that water reaches a drain.

## Version 0.2 commissioning

On September 7, 2026, one requested purge produced a fresh draining-on sample, followed by draining-off. **Last command** retained `device_reported` after draining cleared. Normal power-off was subsequently confirmed by a fresh sample; no fault was reported. Locator on/off and power feedback were also checked. This establishes the feedback path on the tested controller, not pump flow or indicator visibility.

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

## Contributor checks

Run `make check` to execute Ruff and the integration tests. Temporary Home Assistant instances and HTTP servers are stopped by the tests. Use sanitized fixtures; never commit account credentials, device/network identifiers, raw packet captures or extracted vendor application code.

When validating a new model, record its exact label/controller generation, app name/version, firmware if known, Home Assistant version, tested integration revision and which individual operations returned fresh feedback. Distinguish supported, experimentally observed and untested operations. Keep installation-specific schedules and room details outside the integration repository.
