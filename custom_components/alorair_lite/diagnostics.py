"""Diagnostics deliberately omit credentials, account and network identifiers."""

from .models import code, number


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    return {
        "transport": "vendor_http_cloud_polling",
        "poll_success": coordinator.last_update_success,
        "sample_stale": coordinator.status_stale,
        "pending_power": coordinator.pending_power,
        "state": {
            key: code(data, key)
            for key in ("powerStatus", "drainStatus", "locateFunction", "defrostingStatus", "errCode")
        },
        "measurements": {
            key: number(data, key)
            for key in ("currentHumidity", "inHumidity", "outHumidity", "inCelsius", "outCelsius")
        },
    }
