# Model and controller compatibility

**Only one AlorAir-Lite-equipped Storm Pro has been live-tested with the cloud integration.** A standalone local endpoint also confirmed power ON/OFF, targets 50% and 55%, continuous-mode restoration, display-unit changes and one warm reconnect on that unit. Other numeric targets have not been exercised locally; the Home Assistant local profile has since been commissioned on that unit, first temporarily and now persistently. ALORAIR's own app compatibility list is broader, but a manufacturer-listed model is a candidate for testing, not a verified integration target. Reviewed September 7, 2026.

## Evidence levels

| Level | Meaning |
| --- | --- |
| Integration tested | Commands and fresh device reports were observed through this integration on a particular unit. |
| Local prototype tested | A standalone local endpoint exercised the specifically listed commands/reconnect behavior. This does not establish the complete Home Assistant local profile or every native field. |
| Manufacturer lists Lite | An official ALORAIR page assigns the model/controller to AlorAir-Lite. This integration has not been tested on that model. |
| Different app or unknown | AlorAir-R/AlorAir-C, non-Wi-Fi equipment, or a variant without sufficient evidence. Outside current support. |

The tested Storm Pro is the Smart Wi-Fi model marketed at 180 PPD saturation / 85 PPD AHAM. The cloud contract was reconstructed from AlorAir-Lite Android 2.0.8, package `com.ruifeng.alorairrliteNew`. Cloud power, target RH, continuous mode, purge, locate and display-unit changes were confirmed through fresh telemetry. There was no independent physical observation. [Cloud test record](LIVE_VALIDATION.md), [manufacturer Storm Pro page](https://www.alorair.com/product-details/alorair-storm-pro-dehumidifier-new)

## Experimental local profile

The observed Lite controller initiates native TCP on port 6100. A device-scoped redirect to a standalone endpoint supported Fahrenheit → Celsius → Fahrenheit and one fresh local connection after a deliberate close. A later local trial confirmed ON and OFF through matching native reports. Its scoped rollback completed cleanly, and fresh cloud OFF state was subsequently verified in Home Assistant. Cloud-originated native traffic established targets 50 → 55 → 50 → 20, with 20 selecting continuous mode. A subsequent standalone local trial completed ON → 50 → 55 → 20 → OFF with matching reports on the same connection. Only targets 50, 55 and continuous 20 were exercised locally; see the [validation record](LIVE_VALIDATION.md#experimental-local-commissioning) for audit status.

Starting with 0.3.1, both Home Assistant profiles register the same 24 entities. Version 0.4.0 raises the number that work locally from six to fourteen by adding the measured inlet/outlet sensors, decoded from the appliance's native status reports. Both power controls share one coordinator, and ON is disabled by default. The dehumidifier keeps the cloud profile's stable identity; faults, purge and locator stay registered but unavailable locally. Review dependent automations against those limits. See [Local control and setup](LOCAL_CONTROL.md).

This evidence is specific to one already-provisioned controller. Neither Lite app compatibility nor a matching enclosure establishes native TCP compatibility. Cold boot, persistent DNS/routing behavior, sustained offline operation and additional model/controller revisions remain unverified. The separate Sentinel/AlorAir-C TCP6200 implementation is not a supported alternative profile here.

## Manufacturer-listed Lite models

ALORAIR's [Lite app page](https://www.alorair.com/alorair-lite-wifi-app) explicitly lists the first seven product names below in its app-compatible section. The two SLGR product pages separately identify their newer controllers as Lite-compatible. These names are not interchangeable with similarly named non-Wi-Fi or earlier-controller versions.

| Exact model / variant | Official product page | Integration evidence |
| --- | --- | --- |
| Storm Pro, Lite-equipped Smart Wi-Fi version | [Storm Pro](https://www.alorair.com/product-details/alorair-storm-pro-dehumidifier-new) | One unit tested; not every production revision. |
| Storm DP **Single-Voltage** | [Storm DP Single-Voltage](https://www.alorair.com/product-details/alorair-storm-dp-single-voltage) | Manufacturer lists Lite; untested here. |
| Storm Ultra, current listing | [Storm Ultra](https://www.alorair.com/product-details/alorair-storm-ultra-new) | Manufacturer lists Lite; untested here. |
| Storm Elite, current listing | [Storm Elite](https://www.alorair.com/product-details/alorair-storm-elite-new) | Manufacturer lists Lite; untested here. |
| Storm **LGR 1250X** | [LGR 1250X](https://www.alorair.com/product-details/alorair-storm-lgr-1250x) | Manufacturer lists Lite; untested here. |
| Storm **LGR 850X** | [LGR 850X](https://www.alorair.com/product-details/alorair-storm-lgr-850x) | Manufacturer lists Lite; untested here. |
| Storm LGR Extreme **Smart App Control** | [Extreme Smart App Control](https://www.alorair.com/product-details/alorair-storm-lgr-extreme-smart-app-control) | Manufacturer lists Lite; untested here. |
| Storm **SLGR 850X**, newer controller | [SLGR 850X](https://alorair.com/product-details/alorair-storm-slgr-850x) | Product page specifies Lite on/after the 2023 cutoff; untested here. |
| Storm **SLGR 1250X**, newer controller | [SLGR 1250X](https://alorair.com/product-details/alorair-storm-slgr-1250x) | Product page specifies Lite on/after the 2023 cutoff; untested here. |

Keep `LGR` versus `SLGR`, the `X` suffix, and `Smart App Control` / Wi-Fi wording when identifying a unit. Capacity alone is insufficient: several different models are marketed at 180 PPD. The site also lists Storm LGR 850/1250 and other variants separately; this matrix does not add them by name similarity.

Manufacturer app compatibility does not prove identical telemetry, humidity ranges or command behavior across these models. The cloud command ranges come from the analyzed Lite app and the tested Storm Pro; validate them on another controller before treating it as supported. The model matrix does not certify the experimental local profile for those models.

## Old R controllers versus newer Lite controllers

The current [Storm Pro](https://www.alorair.com/product-details/alorair-storm-pro-dehumidifier-new), [SLGR 850X](https://alorair.com/product-details/alorair-storm-slgr-850x) and [SLGR 1250X](https://alorair.com/product-details/alorair-storm-slgr-1250x) pages give this manufacturing-date guidance:

- Before **April 30, 2023**: AlorAir-R.
- **On or after April 30, 2023**: AlorAir-Lite.
- Follow the connection guide supplied with the unit.

Apply that guidance to the relevant Wi-Fi model/controller, not to every product ALORAIR made after that date. The manufacturer's [compatibility notice](https://www.alorair.com/important-app-compatibility-notice) says the Wi-Fi-chip generations are incompatible and prioritizes the in-box guide. Its [2023 app comparison](https://www.alorair.com/blog/spotting-the-difference-old-vs-new-alorair-apps/) describes the older R controller as Hiflying AP-based and says migration to Lite requires replacing the operation panel/main control board. Installing a different phone app is not a hardware upgrade.

The Lite page provides both small- and large-display setup guides; display size alone is therefore not a Lite/R discriminator. Older equipment with a replacement controller needs the replacement panel's documentation. Replacement-controller combinations have not been tested here. [ALORAIR Lite setup guides](https://www.alorair.com/alorair-lite-wifi-app)

## AlorAir-C is a separate family

ALORAIR's [app download page](https://alorair.com/app-download) separates commercial/restoration equipment using **Lite** from crawlspace equipment using **C**. Explicit official C examples include:

| Model / Wi-Fi variant | Official evidence | This integration |
| --- | --- | --- |
| Sentinel **HD55S Wi-Fi** | [HD55S specification](https://alorair.com/pdf_files/product_guides/233/AlorAir-Sentinel-HD55S-WiFi-Specification.pdf) names AlorAir-C. | Not supported/tested. |
| Sentinel **HDi65S Wi-Fi** | [HDi65S specification](https://alorair.com/pdf_files/product_guides/232/AlorAir-Sentinel-HDi65S-WiFi-Specification.pdf) names AlorAir-C. | Not supported/tested. |
| Sentinel **HD35P Wi-Fi** | [ALORAIR-C connection guide](https://www.alorair.com/assets/website/bestAppBox/pdf/AlorAir-C%20Sentinel%20HD35P%20Specification%20Outline%2020250820.pdf) specifies AlorAir-C. | Not supported/tested. |

Do not infer Lite support because a Sentinel has Wi-Fi, uses a similar Bluetooth name or appears in a site's navigation menu. ALORAIR also describes a [2025 C-app update](https://www.alorair.com/blog/2025-alorair-wifi-app-upgrade-bigger-smarter-better/); “new app” does not necessarily mean Lite. Other Sentinel/C variants have not been exhaustively mapped here.

## Regional and revision limits

The model evidence above comes from ALORAIR's `.com` catalog and its linked specifications. It is not a worldwide SKU/controller matrix. No alternate region's account routing, voltage variant, distributor-specific model, dual-voltage DP or replacement-board combination has been verified with this integration. A matching case, color, capacity or base model name is insufficient evidence.

The cloud profile uses the tested `online-app1.toovem.com` Lite endpoint and has no regional-server selector. The local profile does not contact that endpoint, but its router setup still depends on identifying the particular device's native connection; no universal vendor destination is assumed. Use the supplied regional connection guide and establish the exact controller/app generation. If it names R or C, or does not identify the generation, seek model/controller confirmation from ALORAIR rather than assuming compatibility.

The current [Storm 80X page](https://www.alorair.com/product-details/alorair-storm-80x) did not establish an explicit Lite assignment in this review. It remains unknown here, as do models absent from this matrix. Absence from this document is not proof that hardware cannot work; it means compatibility has not been established.

## Reporting another model

Useful evidence includes the exact model suffix, manufacture date or controller revision, app name/version, sales region, Home Assistant version, selected cloud/local profile and which controls returned fresh feedback. State whether physical behavior was independently observed. Redact the serial number, MAC, IP, account email and tokens before sharing. Do not report a successful login, TCP connection or generic acknowledgement as proof that every control works.
