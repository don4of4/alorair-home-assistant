# Security and privacy

This unofficial integration offers a default cloud profile and an opt-in experimental local profile. Both use unencrypted protocols. Cloud credentials, local listener exposure and the ability to deliver a stop during an outage need different treatment; neither profile automatically falls back to the other.

## Vendor transport

The verified endpoint is `http://online-app1.toovem.com:8081/rest/api/`. A usable HTTPS equivalent has not been established, so setup requires an explicit acknowledgement of unencrypted HTTP.

The login password uses the app's RSA encoding, but the complete connection is not protected by TLS. Account identity, bearer tokens, telemetry and commands are exposed to parties able to inspect or alter that traffic. An IoT VLAN does not encrypt the cloud path. Use a unique vendor-account password.

The client refuses redirects and environment-proxy forwarding. Requests are limited to known endpoints. Optional [experimental cloud actions](EXPERIMENTAL.md) are disabled by default and must be enabled for the selected device. They allow specific history/filter/firmware-information reads and name/location/filter-maintenance changes; they do not expose arbitrary opcodes, firmware installation, pairing or access-setting changes.

## Stored credentials

For cloud entries, Home Assistant stores the account email/password and device identity in its standard config-entry storage. Treat the configuration directory, especially `.storage`, as sensitive plaintext data. Masking the password in the UI does not encrypt it at rest. Session tokens stay in memory and are reacquired after restart.

A local entry stores the selected device identity, source IPv4 and listener settings, without vendor account credentials. Reconfiguring a cloud entry as local replaces its active credential-bearing configuration; it does not erase historical backups or revoke the vendor account/device binding. Network addresses and the MAC remain private configuration data.

Protect access to the Home Assistant host and its backups. Home Assistant supports encrypted backups, but exports/downloads may be decrypted depending on the route used. Keep backup encryption enabled where supported, protect downloaded archives and store the recovery key separately. See the official [backup guidance](https://www.home-assistant.io/common-tasks/general/#backups) and [backup emergency kit](https://www.home-assistant.io/more-info/backup-emergency-kit/).

Enter credentials through the integration UI and use its reauthentication flow after changing the password. Keep them out of YAML, shell commands, Git URLs and issues.

## Device targeting and commands

Cloud setup requires the owning account and complete Wi-Fi MAC. Account and device-detail checks keep commands tied to that exact unit. Shared/guest units are outside initial support. Local setup instead checks the configured source IPv4 and full native-frame MAC; its trust boundary is described below. Duplicate configured device identities are rejected across profiles.

Commands are serialized and uncertain delivery is not blindly replayed. Reported state comes from fresh feedback, and a three-minute minimum off interval limits rapid restarts. Cloud or Home Assistant outages can prevent a stop command from arriving; continuous mode may keep the unit drying until control returns. The firmware retains its own protection and shutdown behavior.

Live validation confirms reported state for one device; it does not independently observe the appliance or establish shutdown behavior during a network outage. Purge produced a reported draining transition; this does not verify water flow or hose condition. Confirm the drain route before using it, and review the limits in [Validation](LIVE_VALIDATION.md).

## Experimental local transport

The local client accepts inbound plaintext TCP on an explicit IPv4 address and port. It has no vendor HTTP connection, account login, outbound forwarding path or automatic cloud fallback. It accepts only the configured source address and exact device identity, with strict frame-length/checksum validation. **These are filtering and framing checks, not cryptographic authentication or encryption.** A party able to impersonate that source and identity could forge telemetry or interfere with commands.

Bind to the intended local interface and restrict access to the selected device at the network firewall. Do not expose the listener through an internet port-forward. Configure router destination NAT only for the identified device connection, preserve its source address at the listener and retain a rollback path. The integration does not install or remove those rules. Changing the device address or network topology requires reviewing the configured scope. See [Local network setup](LOCAL_CONTROL.md#network-requirements).

Local ON is disabled by default behind **Allow experimental local power-on**, shared by the dehumidifier and Power switch. A standalone local endpoint has confirmed ON/OFF through native reports, and the HA profile has since been commissioned on that unit. A standalone local sequence also confirmed targets 50%/55% and restoration to continuous mode; this does not validate every numeric target. Fresh reported ON is required before ordinary humidity/mode changes. A safety OFF can be attempted on a verified connected session with stale telemetry; a matched later report is still required before claiming confirmation. If the connection is lost, no local command can reach the appliance. Unloading an entry or removing its route does not itself stop an enabled unit.

The standalone local evidence covers power ON/OFF, targets 50%/55%, continuous-mode restoration, display selection and one warm reconnect. See the [validation record](LIVE_VALIDATION.md#experimental-local-commissioning) for the latest trial audit status. The power trial's temporary routing was removed cleanly and fresh cloud OFF state returned afterward. That rollback does not establish shutdown during connection loss, long-running autonomous drying, cold boot, or a complete Home Assistant migration. Native fault fields remain unmapped; no fault entity is not evidence of a fault-free appliance. The measured fields decoded in 0.4.0 are unauthenticated device-reported telemetry and could be forged by a successful impersonator. The vendor app may cease updating when the device connection is redirected locally; restoring routing and restoring a cloud entry are separate operations.

## Repository and diagnostics

Private repositories and forks still must not contain secrets. Do not commit:

- Passwords, bearer tokens, cookies or Home Assistant authentication material.
- Real account email, device MAC/IP, Wi-Fi names/passwords or location details.
- Raw account/device responses, packet captures or config-entry exports.
- Vendor APKs, app bundles, decompiled source or extracted assets.

The clients expose selected telemetry/metadata and use credential-safe errors. Native frame payloads and captures are not part of normal entity diagnostics. Device names and diagnostic timestamps can still reveal identifying information; review diagnostic files and log excerpts before sharing. Do not enable broad HTTP debug logging while authenticating, and keep raw TCP captures or router exports out of shared diagnostics.

Report a security issue privately to the repository owner with a minimal redacted description. Do not include a working token or another account's data to demonstrate it. Rotate exposed credentials promptly and remove exposed artifacts from their original storage and any shared copies.
