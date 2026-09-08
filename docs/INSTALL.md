# Installation

Install the custom component, then choose **AlorAir-Lite cloud** or **Experimental local connection** through Home Assistant's UI in a build containing both profiles. The cloud profile is the default for existing entries. Home Assistant 2026.6.2 and one Lite-equipped Storm Pro have been tested with cloud control; see [Compatibility](COMPATIBILITY.md) before installing for another model or controller generation. The experimental local profile has separate [setup and validation limits](LOCAL_CONTROL.md); standalone endpoint tests do not mean the HA profile has been commissioned on hardware.

**Current availability:** this public repository can be added to **HACS** (Home Assistant Community Store) as a custom integration repository. Version **0.3.0** includes the default cloud profile and opt-in experimental local control. It is not in HACS's default catalog. HACS download and migration on a running Home Assistant installation have not yet been exercised.

## Requirements

- Home Assistant **2026.6.2 or newer** and administrator access. This is the minimum declared in `hacs.json` and the version used in the test suite; older versions are unverified. Home Assistant OS/Container manages its own Python runtime.
- For **Cloud**, an ALORAIR account that owns the device and already works in **AlorAir-Lite**. Both profiles require an already-provisioned Wi-Fi device; this integration does not provision Wi-Fi, change the controller or bind a new device to an account. **Experimental local** instead requires explicit bind/device IPv4 addresses, a TCP listener port, and device-scoped router redirection. It does not use account credentials. See [Local network requirements](LOCAL_CONTROL.md#network-requirements).
- The complete Wi-Fi MAC for that unit, obtained from its label or your router. The integration accepts 12 hex digits or six colon/hyphen-separated pairs and matches the complete identity. The cloud may show a 24-character number with twelve leading zeros; enter the ordinary Wi-Fi MAC, not that padded cloud number. An IP address or Bluetooth discovery name is not a substitute.
- A recent Home Assistant backup. Manual installation also requires read access to the repository or a release package and a trusted way to copy files into Home Assistant's configuration directory. See Home Assistant's [configuration directory guidance](https://www.home-assistant.io/docs/configuration/) and [file-access guidance](https://www.home-assistant.io/common-tasks/os/#configuring-access-to-files).

## HACS installation

This is the preferred installation and update method. The project uses a **HACS custom repository**; it is not included in HACS's default catalog.

1. Install and configure HACS using its official [download](https://hacs.xyz/docs/use/download/download/) and [initial configuration](https://hacs.xyz/docs/use/configuration/basic/) instructions, if it is not already present.
2. Open **HACS** in Home Assistant. Select the top-right **⋮ → Custom repositories**.
3. Enter `https://github.com/don4of4/alorair-home-assistant`, select type **Integration**, and choose **Add**.
4. Find **ALORAIR Lite** in HACS, open it, and choose **Download**. Select the latest stable release, review the displayed version, and complete the download.
5. Restart Home Assistant, then follow [Add the account and unit](#add-the-account-and-unit).

HACS downloads the component files; account setup still happens separately in **Settings → Devices & services**. See HACS's official [custom repository instructions](https://hacs.xyz/docs/faq/custom_repositories/). Do not add this URL to the Home Assistant app/add-on store.

### Move an existing manual install to HACS

Back up Home Assistant and follow the HACS download steps above. Download the integration through HACS even if its files already exist: HACS [does not automatically adopt manually installed files](https://hacs.xyz/docs/faq/existing_elements/).

Keep the existing **ALORAIR Lite** entry in **Settings → Devices & services**. Do not delete it or configure a second copy. Both methods install the same `alorair_lite` integration directory; downloading through HACS and restarting should retain the existing account entry, device/entity identities and automations. Verify the existing entities after the restart. This migration procedure has not yet been exercised against a public HACS installation.

## Manual installation

### Download a release

Download `alorair-lite-<version>.zip` and `SHA256SUMS.txt` from the same version in [Releases](https://github.com/don4of4/alorair-home-assistant/releases). With both downloads in the same directory, verify them using `shasum -a 256 -c SHA256SUMS.txt` on macOS or `sha256sum -c SHA256SUMS.txt` on Linux. Extract the ZIP to a temporary directory; it contains `custom_components/alorair_lite/`. Copy the complete integration folder as described below.

Alternatively, clone the published release with Git; no repository credentials are required:

```sh
git clone https://github.com/don4of4/alorair-home-assistant.git
cd alorair-home-assistant
git checkout v0.3.0
```

The checkout pins a published release instead of installing changes from `main`; choose a newer published release tag when appropriate.

### Copy the integration

Find Home Assistant's actual configuration directory: the directory containing `configuration.yaml`. For Home Assistant OS it is exposed as `/config` through supported file-access tools; for Container, use the host directory mapped to `/config`. Do not assume that `/config` on your laptop is the server's directory.

Copy the **entire** `custom_components/alorair_lite` directory into `<configuration directory>/custom_components/`. Use your existing authenticated file-transfer method if Home Assistant runs on another machine. The final layout must be:

```text
<configuration directory>/
  configuration.yaml
  custom_components/
    alorair_lite/
      __init__.py
      manifest.json
      config_flow.py
      coordinator.py
      brand/
        icon.png
      translations/
```

The tree shows selected files only; copy the complete directory.

If that configuration directory is already mounted on your computer, these commands perform the copy. Replace the example path with the mounted directory before running them:

```sh
HA_CONFIG_DIR="/path/to/mounted/home-assistant-config"
mkdir -p "$HA_CONFIG_DIR/custom_components"
cp -R custom_components/alorair_lite "$HA_CONFIG_DIR/custom_components/"
```

Do not copy the repository itself as an extra nested directory, and do not copy account credentials or research artifacts. Home Assistant's [integration file-structure documentation](https://developers.home-assistant.io/docs/creating_integration_file_structure/) describes the `custom_components/<domain>` layout.

Restart Home Assistant after copying. Reloading YAML does not load newly added Python integration code. After the restart, check **Settings → System → Logs** for `alorair_lite` import or dependency errors.

## Add the account and unit

1. Open **Settings → Devices & services → Add integration** and search for **ALORAIR Lite**.
2. Choose **AlorAir-Lite cloud**, then enter your AlorAir-Lite account email/password and the complete Wi-Fi MAC from the requirements above.
3. Read and acknowledge the vendor's unencrypted HTTP requirement. Without that acknowledgement, the integration does not connect. See [Security](SECURITY.md).
4. Complete setup. The integration checks the owned-device list and binds the entry to the exact device identity; it does not select the first account device or silently change the app's selected unit.
5. Open the created device page. Record the actual entity IDs Home Assistant assigned. Entity IDs depend on the device name and existing entities, so do not assume an example ID is yours.

The dehumidifier entity provides power, target humidity and `auto`/`continuous` modes. The unit needs to report on before changing humidity or mode. After setup or reload, a new start can be rejected for up to three minutes by restart protection; an already-running unit can still be stopped normally.

Selecting `auto` restores the last numeric target. If Home Assistant starts while the unit is continuous, the integration restores that target from Home Assistant's saved entity state when available. The fallback is 50% only when no valid numeric target or saved history is available, such as the initial installation. Set an explicit target if you want a different value.

Polling defaults to 30 seconds. The integration's **Configure** options allow 15–300 seconds; changing the options reloads the entry and begins a new conservative restart delay. Keep the default while validating the device's sample cadence.

**Enable experimental cloud actions** is off by default in **Configure**. Enabling it permits the **Experimental action** service for that selected Home Assistant device: history/filter/firmware-information reads and specific name/location/filter-maintenance changes. It does not enable raw commands, firmware installation, pairing or access-setting changes. See [Experimental actions](EXPERIMENTAL.md) for the allowlisted operations. Ordinary purge and locate controls do not require this option.

The device page also includes **Fresh device sample**, **Device sample time**, **Fault codes**, **Last command** and **Refresh status** diagnostics/actions. Last command distinguishes awaiting feedback, device-reported results, cloud acknowledgements and uncertain/rejected/unobserved outcomes; none is an independent physical measurement. The dehumidifier's attributes distinguish `pending_power`, `restart_delay_remaining`, `vendor_update_time` and `cloud_polled_at`. Grain-per-pound, reported working-time and coil-temperature entities are disabled by default; enable them on the device's entity page only if useful, and preserve the protocol caveats about working-time reset behavior and coil units.

Confirm that power, intake humidity, sample time and fault information match the intended unit. Start with a reversible humidity change while the unit reports on, then restore the original setting and check fresh feedback. Use **Purge drain** only with a connected drain route ready to receive water. A draining report confirms the reported mode, not actual flow. [Validation](LIVE_VALIDATION.md) describes the tested controls and remaining limits.

The integration creates device entities only. Any Home Assistant dashboard or automation using those entities is configured separately.

### Experimental local setup or reconfiguration

Version 0.3.0 includes this opt-in experimental profile. Earlier releases, including v0.2.2, contain the cloud profile only.

Choose **Experimental local connection** to configure an inbound TCP listener using an explicit bind IPv4, device IPv4, port (default 6100) and full Wi-Fi MAC. Router redirection and reachability must be prepared separately; this form does not discover or configure the appliance network. Follow [Experimental local control](LOCAL_CONTROL.md) before changing transport.

For a device already configured with Cloud, use the existing entry's **Reconfigure** action. Duplicate device identities are rejected. Reconfiguration preserves the entry/device identity and the dehumidifier's unique identity, and replaces cloud credentials with local settings. The local profile creates six entities: dehumidifier, Power switch, Temperature display, Fresh device sample, Device sample time and Last command. Target/auto/continuous controls are implemented, but intake humidity remains unknown and fault, purge and locator entities are absent. Review dependent automations and actual entity IDs before migration. ON requires the local power option, disabled by default. A standalone local endpoint has confirmed ON/OFF, targets 50%/55% and continuous-mode restoration. A persistent Home Assistant installation also confirmed ON/OFF while retaining continuous mode. Each installation still requires appliance commissioning; an earlier mode-change failure, other numeric targets, cold boot and sustained offline operation remain unresolved or unverified. There is no automatic cloud fallback. See [Reconfiguration and rollback](LOCAL_CONTROL.md#reconfigure-an-existing-cloud-entry).

## Updates

Before updating, pause any automations that issue commands to this device and request a normal stop if appropriate. Back up Home Assistant. Keep the previous release until state/control checks pass. Restart Home Assistant after updating integration files; a config-entry reload does not load changed Python code. Re-enable paused automations after verification.

### HACS-managed installation

Use the update offered by HACS/Home Assistant, review the release notes, download the new version, and restart Home Assistant. HACS provides [update entities](https://hacs.xyz/docs/use/entities/update/) for managed repositories. For a rollback, open the integration in HACS and use **⋮ → Redownload → Need a different version?** to choose the prior release, then restart. See the [HACS download and version selection guide](https://hacs.xyz/docs/use/repositories/dashboard/#downloading-a-specific-version-of-a-repository). Restore your Home Assistant backup if a version change also requires restoring configuration.

### Manual installation

Download the new release package, or run `git fetch --tags` and `git checkout <published-release-tag>` in your source checkout. Replace the complete `custom_components/alorair_lite` directory with the new version and restart Home Assistant. Move the previous folder outside `custom_components` before copying so removed files cannot linger. To roll back, replace it with the saved prior folder or release and restart.

## Removal

First pause any automations using its entities and request a normal device stop if that is the desired final state. For a local entry, also plan removal of its device-scoped routing rule and restoration of the original network path. Delete the ALORAIR Lite entry under **Settings → Devices & services**. For a HACS-managed installation, remove the downloaded integration through HACS; for a manual installation, remove only its `custom_components/alorair_lite` directory. Restart Home Assistant. Removing an integration does not itself send a stop command or remove router rules.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| HACS cannot add the repository | Use the exact GitHub URL above and type **Integration**; check GitHub connectivity and HACS logs. Private forks cannot be installed through HACS. |
| HACS rejects the Home Assistant version | Upgrade to the declared minimum, 2026.6.2. Older Home Assistant versions are unverified. |
| Files already exist but HACS shows no installed version | Download through HACS and restart; it does not adopt a manual install automatically. Keep the existing integration entry. |
| Integration is absent from Add integration | Check the exact directory layout, `manifest.json`, file permissions and a completed restart; then inspect logs. |
| Login rejected | Verify this account works in the current Lite app. Use Home Assistant's reauthentication flow after changing the password. Do not repeatedly retry guessed credentials. |
| Unit not found | Use the complete Wi-Fi device number and its owning account. Shared/guest devices and other app families are outside initial support. |
| Restart protection error | Wait for the displayed delay after off/setup/reload; do not rapidly retry power commands. |
| Humidity/mode action rejected while off | Turn on, wait for reported on, then change the setting. |
| A command remains pending | Wait for device feedback and inspect timestamps. A successful request does not establish that the physical unit acted. |
| Entities become unavailable | Check vendor connectivity, credentials and fresh device samples. A working phone app may be using cached readings too. |
| Local entry has no samples or shows unknown intake humidity | Check [local routing and troubleshooting](LOCAL_CONTROL.md#troubleshooting-and-remaining-work). Native intake humidity is not mapped; a reported target is not a humidity measurement. The local profile does not query cloud status as a fallback. |
| Dependency/import error after an HA upgrade | Check logs and the tested version in [Live validation](LIVE_VALIDATION.md); restore the previous integration/HA backup if needed. |

When reporting a failure, include the integration commit/version, Home Assistant version, affected action and a redacted error. Exclude raw API responses, email, tokens, MAC addresses, IP addresses and configuration-entry exports.

## Development environment

These steps are for contributors; they are not needed when copying the integration into an existing Home Assistant installation.

Use Python 3.14.2 or newer and the package index approved for your machine:

```sh
uv sync
make check
```

`make check` runs lint, formatting checks and tests. `requirements-dev.txt` is the exported, hash-pinned environment used by CI. Dependency changes belong in `pyproject.toml` through `uv add`; regenerate the export after changing them:

```sh
uv export --all-groups --no-emit-project --format requirements-txt --output-file requirements-dev.txt
```

The root `LICENSE` is the authoritative MIT license text. After changing it, run `make sync-license` to update the copy shipped inside `custom_components/alorair_lite/`; `make check` rejects mismatched copies. The component's `NOTICE.md` accompanies the manufacturer icon in both manual and HACS downloads and excludes that artwork from the code license.

The index-specific local `uv.lock` is excluded from Git. Development uses `aiooui==0.1.7`, satisfying the upstream `>=0.1.1` requirement while retaining exact Home Assistant 2026.6.2. See the [dependency declaration](https://github.com/Bluetooth-Devices/bluetooth-adapters/blob/main/pyproject.toml) and [maintainer changelog](https://github.com/Bluetooth-Devices/aiooui/blob/main/CHANGELOG.md). This development constraint does not replace Bluetooth packages in an existing Home Assistant installation.

### HACS distribution checks

`hacs.json` declares the display name and minimum Home Assistant version. HACS uses the source tree at the selected release tag, with all runtime files in `custom_components/alorair_lite/`. Leave `zip_release` unset: the attached release ZIP has a wrapper directory for manual installation, whereas HACS's ZIP-release mode expects a different archive layout.

Local/CI distribution checks verify metadata consistency, the component layout and the bundled icon. The separate **HACS public repository validation** job uses the official HACS validator and runs only on public repositories; a skipped job while private is not a HACS validation pass. Actual HACS download, update and migration on a running Home Assistant installation remain unverified. See the [HACS publishing requirements](https://hacs.xyz/docs/publish/start/) and [integration requirements](https://hacs.xyz/docs/publish/integration/).

When publishing a new version, keep `manifest.json`, the project version and the release tag consistent, run the checks, and create a GitHub release. A tag alone does not publish a new version to HACS when the project uses releases. Include `hacs.json` and the brand assets in the tagged source tree.
