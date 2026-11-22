"""The WEG Solar Portal integration.

Each config entry represents ONE plant. Devices (inverters) are attached to
the plant device, so Home Assistant shows every inverter inside its plant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    discovery_flow,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import RateLimiter, WegSolarApiClient, WegSolarApiError
from .const import (
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_PLANT_ID,
    CONF_PLANT_NAME,
    DOMAIN,
    PLANT_DISCOVERY_INTERVAL,
)
from .coordinator import WegSolarConfigEntry, WegSolarCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


def get_client(hass: HomeAssistant, api_key: str, api_secret: str) -> WegSolarApiClient:
    """Return an API client sharing one rate limiter per API key."""
    limiters: dict[str, RateLimiter] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "limiters", {}
    )
    limiter = limiters.setdefault(api_key, RateLimiter())
    return WegSolarApiClient(
        api_key=api_key,
        api_secret=api_secret,
        session=async_get_clientsession(hass),
        limiter=limiter,
    )


async def async_setup_entry(hass: HomeAssistant, entry: WegSolarConfigEntry) -> bool:
    """Set up one WEG Solar plant from a config entry."""
    api_key = entry.data[CONF_API_KEY]
    api_secret = entry.data[CONF_API_SECRET]
    client = get_client(hass, api_key, api_secret)

    coordinator = WegSolarCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _async_cleanup_devices(hass, entry, coordinator)
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: _async_cleanup_devices(hass, entry, coordinator)
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    domain_data = hass.data.setdefault(DOMAIN, {})
    key_entries: dict[str, set[str]] = domain_data.setdefault("key_entries", {})
    discovery_unsubs: dict[str, CALLBACK_TYPE] = domain_data.setdefault(
        "discovery_unsubs", {}
    )
    key_entries.setdefault(api_key, set()).add(entry.entry_id)

    if api_key not in discovery_unsubs:
        hass.async_create_background_task(
            async_discover_other_plants(hass, api_key, api_secret, client),
            f"{DOMAIN}_discover_plants_{api_key[:8]}",
        )

        @callback
        def _schedule_discovery(_now: datetime, key: str = api_key) -> None:
            """Run hourly plant discovery using any active entry for this key."""
            active_ids = key_entries.get(key, ())
            for candidate in hass.config_entries.async_entries(DOMAIN):
                if (
                    candidate.entry_id in active_ids
                    and candidate.data.get(CONF_API_KEY) == key
                ):
                    cand_secret = candidate.data[CONF_API_SECRET]
                    cand_client = get_client(hass, key, cand_secret)
                    hass.async_create_background_task(
                        async_discover_other_plants(
                            hass, key, cand_secret, cand_client
                        ),
                        f"{DOMAIN}_discover_plants_{key[:8]}",
                    )
                    break

        discovery_unsubs[api_key] = async_track_time_interval(
            hass,
            _schedule_discovery,
            timedelta(seconds=PLANT_DISCOVERY_INTERVAL),
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: WegSolarConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        key_entries: dict[str, set[str]] = domain_data.get("key_entries", {})
        discovery_unsubs: dict[str, CALLBACK_TYPE] = domain_data.get(
            "discovery_unsubs", {}
        )
        api_key = entry.data[CONF_API_KEY]
        entries = key_entries.get(api_key)
        if entries is not None:
            entries.discard(entry.entry_id)
            if not entries:
                key_entries.pop(api_key, None)
                if unsub := discovery_unsubs.pop(api_key, None):
                    unsub()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: WegSolarConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow manual removal of devices that no longer exist in the API."""
    coordinator = entry.runtime_data
    active = {coordinator.plant_id, *coordinator.data.inverters}
    return not any(ident[1] in active for ident in device.identifiers if ident[0] == DOMAIN)


@callback
def _async_cleanup_devices(
    hass: HomeAssistant, entry: WegSolarConfigEntry, coordinator: WegSolarCoordinator
) -> None:
    """Remove replaced / vanished inverters (and other plants) from this entry."""
    if coordinator.data is None:
        return
    active = {coordinator.plant_id, *coordinator.data.inverters}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ids = {ident[1] for ident in device.identifiers if ident[0] == DOMAIN}
        if ids and not ids & active:
            _LOGGER.info("Removing obsolete/replaced device %s", device.name)
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_discover_other_plants(
    hass: HomeAssistant,
    api_key: str,
    api_secret: str,
    client: WegSolarApiClient,
) -> int:
    """Offer other plants of the same API key as discovered entries."""
    try:
        plants = await client.async_get_plants()
    except WegSolarApiError as err:
        _LOGGER.debug("Could not list plants for discovery: %s", err)
        return 0

    configured = {
        e.unique_id for e in hass.config_entries.async_entries(DOMAIN, include_ignore=True)
    }
    in_progress = {
        flow.get("context", {}).get("unique_id")
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    }

    created = 0
    for plant in plants:
        plant_id = plant.get("_id")
        if not plant_id or plant_id in configured or plant_id in in_progress:
            continue
        discovery_flow.async_create_flow(
            hass,
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={
                CONF_API_KEY: api_key,
                CONF_API_SECRET: api_secret,
                CONF_PLANT_ID: plant_id,
                CONF_PLANT_NAME: plant.get("name") or plant_id,
            },
        )
        created += 1
    return created


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 entries (one entry for all plants) to v2 (one entry per plant)."""
    if entry.version > 2:
        return False

    if entry.version == 1:
        client = get_client(hass, entry.data[CONF_API_KEY], entry.data[CONF_API_SECRET])
        try:
            plants = await client.async_get_plants()
        except WegSolarApiError as err:
            _LOGGER.error("Migration failed, could not list plants: %s", err)
            return False
        if not plants:
            _LOGGER.error("Migration failed: no plants available for this API key")
            return False

        first = plants[0]
        plant_id = first.get("_id")
        plant_name = first.get("name") or plant_id

        # Remove entities that no longer exist in v2
        ent_reg = er.async_get(hass)
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if ent.unique_id.endswith(("_device_count", "_alarm_count", "_device_energy")):
                ent_reg.async_remove(ent.entity_id)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_PLANT_ID: plant_id, CONF_PLANT_NAME: plant_name},
            title=plant_name,
            unique_id=plant_id,
            version=2,
        )
        _LOGGER.info(
            "Migrated WEG Solar entry to plant %s; other plants will be offered "
            "as discovered integrations",
            plant_name,
        )

    return True
