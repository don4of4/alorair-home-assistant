# ALORAIR Lite for Home Assistant

An unofficial Home Assistant integration for **AlorAir-Lite** dehumidifiers. The default cloud profile exposes device controls and telemetry through standard Home Assistant entities. An opt-in [experimental local profile](docs/LOCAL_CONTROL.md) accepts the unit's native TCP connection after device-specific network setup.

**The cloud profile was live-tested with one Storm Pro using AlorAir-Lite on Home Assistant 2026.6.2.** Power, humidity, continuous mode, purge, display units and locate were confirmed through fresh device reports. Other models remain unverified. This is telemetry confirmation, not independent physical observation. See [Compatibility](docs/COMPATIBILITY.md) and [Validation](docs/LIVE_VALIDATION.md).

**Version 0.4.0** provides opt-in experimental local control; the cloud profile remains the default. A persistent Home Assistant installation with device-scoped routing, managed local DNS and an enabled device WAN block confirmed ON/OFF while retaining continuous mode, without retries. Earlier testing found an unconfirmed mode-change request, so command reliability and long-running operation remain open limitations. The local transport reports measured inlet/outlet humidity, temperature, specific humidity and draining state from the appliance's native status reports. Faults, purge and locator remain unavailable locally; cold boot and other controllers are unverified. [Local setup and evidence](docs/LOCAL_CONTROL.md)

## Get started

1. Check your exact model and controller against the [compatibility guide](docs/COMPATIBILITY.md). The device must already work in AlorAir-Lite under your owning account.
2. Follow [Installation](docs/INSTALL.md) for **HACS** (Home Assistant Community Store) or a manual install, then add **ALORAIR Lite** through **Settings → Devices & services** and choose **AlorAir-Lite cloud**. Experimental local setup has separate [network requirements](docs/LOCAL_CONTROL.md#network-requirements).
3. Open the created device page for power, humidity, mode and telemetry. Use the entity IDs assigned by your installation.

**Install with HACS:** add `https://github.com/don4of4/alorair-home-assistant` as a custom repository of type **Integration**, then download **ALORAIR Lite** and restart Home Assistant. This public, MIT-licensed integration is not in HACS's default catalog. See the [installation and migration guide](docs/INSTALL.md) for complete steps, including manual installation.

## Switching between cloud and local

The 0.3.1 update registers the **same 24 entities** in both profiles, including optional diagnostics disabled by default. Use the existing integration entry's **Reconfigure** action: entity names, IDs and user customizations remain attached to the same device. The Power switch is available in either profile and shares the dehumidifier's command handling. Since 0.4.0 the measured inlet/outlet sensors and Draining indicator report in either profile, and the dehumidifier's current humidity is the inlet reading rather than its target.

Features that the local protocol does not yet support, such as fault reporting, purge and locate, stay registered as **unavailable**, with `unavailable_reason: not_supported_by_local_connection`. They resume through the same entities after switching to a working cloud connection. This preserves dashboard and automation references; it does not supply missing readings or enable unsupported controls. Local mode never falls back to the cloud. See [switching connections](docs/INSTALL.md#switching-connections).

## Cloud capabilities

| Feature | Behavior |
| --- | --- |
| Power | Native on/off commands; reported state is kept separate from pending commands. |
| Humidity | 25–80% target in 5% steps, plus continuous mode. The device must be on before changing its target or mode. |
| Telemetry | Available intake/outlet humidity and temperature, specific humidity, fault codes and device state. Missing measurements remain unknown. |
| Additional controls | Purge drain, locate, Celsius/Fahrenheit and specific-humidity display units. Purge feedback confirms reported draining state, not water flow. |
| Optional cloud actions | Opt-in history, filter and firmware-information reads, plus selected name/location and filter-maintenance changes. See [Experimental actions](docs/EXPERIMENTAL.md). |
| Restart protection | At least three minutes off before starting; loading/reloading the integration begins a conservative delay. Device firmware retains its own protection and shutdown behavior. |

Polling defaults to 30 seconds. The integration waits for fresh device feedback instead of displaying a requested change as completed. **Last command** distinguishes feedback from a cloud acknowledgement or an uncertain result. Samples older than five minutes make device controls unavailable. The tested unit continued reporting while off; reporting cadence on other firmware is unverified. [Protocol details](docs/PROTOCOL.md)

There are no fan-speed, fan run-on, calibration or device-timer controls. This repository supplies the integration only; it does not install schedules, dashboards or external-sensor automations. A smart plug is not required.

## Connection and compatibility

The cloud contract was reconstructed from AlorAir-Lite Android 2.0.8. ALORAIR lists additional Storm models for its Lite app, but that does not establish compatibility with this integration. AlorAir-R and AlorAir-C devices are outside current support. Check the [model and controller matrix](docs/COMPATIBILITY.md).

The cloud endpoint uses **unencrypted HTTP**, acknowledged during setup. Home Assistant stores the account password in its configuration; session tokens stay in memory. The local transport uses unencrypted TCP with source-IP and device-identity checks, without cryptographic authentication. Protect the configuration, backups and local network, and read [Security](docs/SECURITY.md). Neither profile can deliver commands over a failed connection, and there is no automatic fallback between them.

## Development

Run `uv sync` and `make check` with Python **3.14.2 or newer**. See [Development environment](docs/INSTALL.md#development-environment) for dependency and CI details.

This project is independent of ALORAIR and Home Assistant. Vendor application binaries, extracted source, credentials and private device identifiers are not distributed here.

## License

Project code and documentation are licensed under the [MIT License](LICENSE). The bundled AlorAir-Lite icon is third-party artwork excluded from that license; see the [packaged notice](custom_components/alorair_lite/NOTICE.md) for its source and attribution. ALORAIR names and artwork identify the supported app family and do not imply endorsement.
