# Experimental cloud actions and feedback

Experimental actions expose additional behavior reconstructed from AlorAir-Lite. They are disabled by default and may vary by controller or service version. The integration accepts only the actions below for a registered, owned device. It does not accept arbitrary URLs, opcodes or account identifiers.

## Enable and invoke

1. Open **Settings → Devices & services → ALORAIR Lite → Configure**.
2. Enable **Enable experimental cloud actions** and save. Reloading the entry starts the normal conservative restart delay.
3. Open **Developer tools → Actions** and select **ALORAIR Lite: Experimental action**.
4. Choose the device and action. Supply only the parameters that action needs. Read actions return structured response data when requested.

Each action verifies ownership again before accessing the cloud record. Disabling experimental actions prevents subsequent calls. These actions do not provision Wi-Fi or change account access. Ordinary power, humidity, purge and locate controls remain available independently of this option.

## Available actions

| Action | Parameters | Effect |
| --- | --- | --- |
| `history` | Optional `period`: day/month/year; `query_date`: YYYY-MM-DD | Reads bounded historical temperature and humidity samples. Defaults to day and the current Home Assistant local date. Vendor period boundaries/time zone are experimental. |
| `operation_time` | Same as history | Reads the vendor's operating-time summary. This is not measured energy use. |
| `filters` | None | Reads filter records attached to the selected device, including filter IDs and life data where available. |
| `filter_detail` | `filter_id` from this device's filters result | Reads metadata for an attached filter. |
| `firmware_check` | None | Reads whether the service reports an available update. Does not install it. |
| `firmware_history` | Optional `page`: 1–10 | Reads one bounded page of firmware history. |
| `rename` | `label` | Sends the app's device-name command and then updates the cloud name. This changes the vendor label, not a Home Assistant entity ID. |
| `location` | `label` | Changes the vendor's location label. This is a text field, not GPS positioning. |
| `extend_filter` | `filter_id`, `extension`: 1/2/3 | Extends an eligible low-life filter reminder by the vendor's week/month/three-month choice. The numeric choice is not hours. |
| `reset_filter` | `filter_id` | Resets the selected filter's maintenance counter. Use after actual filter service; this does not clean or replace a filter. |

Metadata changes and maintenance actions are explicit writes. They are not run by setup, status refresh or the firmware check. Rename is a two-request vendor operation; a partial or uncertain result is reported without automatically repeating it. Other uncertain writes are also not replayed.

Only sanitized, bounded fields are returned. An unsupported response shape produces an error instead of a guessed measurement. An empty history result is different from an unavailable service. Not all manufacturer-listed models have been commissioned; consult [Compatibility](COMPATIBILITY.md) and [Validation](LIVE_VALIDATION.md).

## What command feedback proves

The **Last command** diagnostic sensor records the most recent command, its request time, acknowledgement and any fresh matching device sample. A brief draining or locating state can therefore remain recorded after its ordinary state entity returns to off. A later command replaces this diagnostic; Home Assistant history can retain earlier states. The diagnostic resets when the integration reloads.

| State | Meaning |
| --- | --- |
| `none` | No command has been recorded since setup/reload. |
| `awaiting_feedback` | A device command was requested; no qualifying device sample has matched yet. |
| `device_reported` | A matching device value appeared in a sample newer than the pre-command baseline and no earlier than the request. |
| `awaiting_response` | An experimental write is in progress. |
| `cloud_acknowledged` | An experimental write received a successful cloud response. This is not a hardware confirmation. |
| `not_observed` | The feedback window expired without a qualifying matching sample. The action may still have occurred between samples. |
| `delivery_uncertain` | Transport/response failure left the outcome uncertain; the command was not blindly replayed. A later qualifying device sample may resolve an ordinary device command. |
| `rejected` | Authentication, validation or an explicit service rejection prevented a confirmed command. |

The integration normally polls every 30 seconds. If a transient event begins and ends between reports, the service may never show it. `device_reported` confirms reported state, not an independent water-flow, pump-current, sound or light measurement. The cloud protocol exposes no verified pump-flow or compressor-relay feedback.

## Purge and locate commissioning

With the drain routed to a suitable destination, turn the unit on and use **Purge drain** once. Watch **Draining** and **Last command**, then compare with physical pump/water behavior if possible. A reported draining transition does not prove the hose is clear or leak-free. The app exposes purge start only; no supported cancel-purge command has been established.

For locate, turn **Locator** on, inspect feedback and the physical indicator, then turn it off. The integration requires the unit on for locate-on. A historical-device app screen omits that guard, but off-state behavior is not established for every controller.

## Unsupported operations

No verified working app command was found for fan speed, fan run-on time, sensor calibration, GPP control targets or a native schedule. Hidden fan/timer markup is not evidence of a working control. Firmware installation, device reset, account binding/unbinding and guest-access settings are intentionally outside this action interface.

These are cloud capabilities. BLE provisioning and potential local TCP interoperability have separate evidence and must not be substituted for a validated local-control protocol. See [Protocol](PROTOCOL.md).
