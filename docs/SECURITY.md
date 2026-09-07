# Security and privacy

This unofficial integration uses the vendor's cloud service. The main considerations are its unencrypted connection, the account credentials stored by Home Assistant, and cloud availability when sending commands.

## Vendor transport

The verified endpoint is `http://online-app1.toovem.com:8081/rest/api/`. A usable HTTPS equivalent has not been established, so setup requires an explicit acknowledgement of unencrypted HTTP.

The login password uses the app's RSA encoding, but the complete connection is not protected by TLS. Account identity, bearer tokens, telemetry and commands are exposed to parties able to inspect or alter that traffic. An IoT VLAN does not encrypt the cloud path. Use a unique vendor-account password.

The client refuses redirects and environment-proxy forwarding. Requests are limited to known endpoints. Optional [experimental cloud actions](EXPERIMENTAL.md) are disabled by default and must be enabled for the selected device. They allow specific history/filter/firmware-information reads and name/location/filter-maintenance changes; they do not expose arbitrary opcodes, firmware installation, pairing or access-setting changes.

## Stored credentials

Home Assistant stores the account email/password and device identity in its standard config-entry storage. Treat the configuration directory, especially `.storage`, as sensitive plaintext data. Masking the password in the UI does not encrypt it at rest. Session tokens stay in memory and are reacquired after restart.

Protect access to the Home Assistant host and its backups. Home Assistant supports encrypted backups, but exports/downloads may be decrypted depending on the route used. Keep backup encryption enabled where supported, protect downloaded archives and store the recovery key separately. See the official [backup guidance](https://www.home-assistant.io/common-tasks/general/#backups) and [backup emergency kit](https://www.home-assistant.io/more-info/backup-emergency-kit/).

Enter credentials through the integration UI and use its reauthentication flow after changing the password. Keep them out of YAML, shell commands, Git URLs and issues.

## Device targeting and commands

Setup requires the owning account and the complete Wi-Fi MAC. Account and device-detail checks keep commands tied to that exact unit. Shared/guest units are outside initial support.

Commands are serialized and uncertain delivery is not blindly replayed. Reported state comes from fresh feedback, and a three-minute minimum off interval limits rapid restarts. Cloud or Home Assistant outages can prevent a stop command from arriving; continuous mode may keep the unit drying until control returns. The firmware retains its own protection and shutdown behavior.

Live validation confirms reported state for one device; it does not independently observe the appliance or establish shutdown behavior during a network outage. Purge produced a reported draining transition; this does not verify water flow or hose condition. Confirm the drain route before using it, and review the limits in [Validation](LIVE_VALIDATION.md).

## Repository and diagnostics

Private repositories and forks still must not contain secrets. Do not commit:

- Passwords, bearer tokens, cookies or Home Assistant authentication material.
- Real account email, device MAC/IP, Wi-Fi names/passwords or location details.
- Raw account/device responses, packet captures or config-entry exports.
- Vendor APKs, app bundles, decompiled source or extracted assets.

The client allowlists returned telemetry/metadata and uses credential-safe errors. Device names and diagnostic timestamps can still reveal identifying information; review diagnostic files and log excerpts before sharing. Do not enable broad HTTP debug logging while authenticating.

Report a security issue privately to the repository owner with a minimal redacted description. Do not include a working token or another account's data to demonstrate it. Rotate exposed credentials promptly and remove exposed artifacts from their original storage and any shared copies.
