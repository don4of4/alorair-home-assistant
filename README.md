# ALORAIR Lite for Home Assistant

An unofficial Home Assistant integration for dehumidifiers connected to the **AlorAir-Lite** cloud service. It exposes device controls and telemetry through standard Home Assistant entities.

**Live-tested with one Storm Pro using AlorAir-Lite on Home Assistant 2026.6.2.** Power, humidity, continuous mode, purge, display units and locate were confirmed through fresh device reports. Other models remain unverified. This is telemetry confirmation, not independent physical observation. See [Compatibility](docs/COMPATIBILITY.md) and [Validation](docs/LIVE_VALIDATION.md).

## Get started

1. Check your exact model and controller against the [compatibility guide](docs/COMPATIBILITY.md). The device must already work in AlorAir-Lite under your owning account.
2. Follow [Installation](docs/INSTALL.md) for **HACS** (Home Assistant Community Store) or a manual install, then add **ALORAIR Lite** through **Settings → Devices & services**.
3. Open the created device page for power, humidity, mode and telemetry. Use the entity IDs assigned by your installation.

**Distribution status:** HACS metadata and instructions are included, but this repository is currently private. [HACS requires a public repository](https://hacs.xyz/docs/faq/private_repositories/), so use the authenticated [manual installation](docs/INSTALL.md#manual-installation) for now. The integration is not in HACS's default catalog. The guide also covers moving an existing manual installation to HACS when public distribution is available.

## Capabilities

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

The vendor endpoint uses **unencrypted HTTP**, acknowledged during setup. Home Assistant stores the account password in its configuration; session tokens stay in memory. Protect the configuration and backups, and read [Security](docs/SECURITY.md). Cloud/network outages can prevent commands from reaching the unit.

## Development

Run `uv sync` and `make check` with Python **3.14.2 or newer**. See [Development environment](docs/INSTALL.md#development-environment) for dependency and CI details.

This project is independent of ALORAIR and Home Assistant. Vendor application binaries, extracted source, credentials and private device identifiers are not distributed here.

The bundled AlorAir-Lite icon comes unmodified from the [manufacturer's website](https://www.alorair.com/assets/website/bestAppBox/images/AlorAir-Lite%201.png). ALORAIR names and artwork belong to their respective owners and identify the supported app family; they do not imply endorsement.
