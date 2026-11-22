"""Diagnostics support for WEG Solar Portal."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_API_SECRET
from .coordinator import WegSolarConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_API_SECRET}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WegSolarConfigEntry
) -> dict[str, Any]:
    """Return diagnostics (raw API payloads) for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "measurement_ids": coordinator._measure_id,  # noqa: SLF001
        "raw_devices": coordinator.raw_devices,
        "raw_alarms": coordinator.raw_alarms,
        "raw_measurements": coordinator.raw_measurements,
        "parsed": {
            "power": data.power,
            "energy_today": data.energy_today,
            "lifetime_energy": data.lifetime_energy,
            "alarms_24h": data.alarms_24h,
            "inverters": {
                k: {kk: vv for kk, vv in asdict(v).items() if kk != "raw"}
                for k, v in data.inverters.items()
            },
            "replaced_devices": data.replaced_devices,
        }
        if data
        else None,
    }
