"""DataUpdateCoordinator for WEG Solar Portal (one coordinator per plant)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    WegSolarApiClient,
    WegSolarApiError,
    WegSolarAuthError,
    WegSolarConnectionError,
    WegSolarRateLimitError,
    is_replaced,
    parse_energy_kwh,
)
from .const import (
    ALARMS_EVERY_N_CYCLES,
    CONF_PLANT_ID,
    CONF_PLANT_NAME,
    DOMAIN,
    STATUS_ONLINE,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Errors that must not be "retried with another id" nor swallowed silently.
_TRANSIENT = (WegSolarRateLimitError, WegSolarConnectionError)


@dataclass
class InverterData:
    """State of one (non-replaced) inverter."""

    device_id: str
    name: str
    model: str | None
    serial_number: str | None
    status: str | None
    lifetime_energy: float | None  # kWh, from devices endpoint
    power: float | None = None  # from measurements
    energy_today: float | None = None  # kWh, from measurements
    last_alarm: dict[str, Any] | None = None
    alarms_24h: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def online(self) -> bool:
        """Return True if the inverter reports online."""
        return (self.status or "").lower() == STATUS_ONLINE


@dataclass
class PlantData:
    """State of the plant handled by this config entry."""

    plant_id: str
    name: str
    inverters: dict[str, InverterData] = field(default_factory=dict)
    replaced_devices: list[dict[str, Any]] = field(default_factory=list)
    power: float | None = None
    energy_today: float | None = None
    lifetime_energy: float | None = None
    alarms: list[dict[str, Any]] = field(default_factory=list)
    alarms_total: int = 0
    alarms_24h: int = 0
    last_alarm: dict[str, Any] | None = None

    @property
    def inverters_online(self) -> int:
        """Number of online inverters."""
        return sum(1 for inv in self.inverters.values() if inv.online)


type WegSolarConfigEntry = ConfigEntry[WegSolarCoordinator]


class WegSolarCoordinator(DataUpdateCoordinator[PlantData]):
    """Fetch all data of one plant."""

    config_entry: WegSolarConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: WegSolarApiClient,
        config_entry: WegSolarConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{config_entry.data[CONF_PLANT_ID]}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
            config_entry=config_entry,
        )
        self.client = client
        self.plant_id: str = config_entry.data[CONF_PLANT_ID]
        self.plant_name: str = config_entry.data.get(CONF_PLANT_NAME) or config_entry.title
        self._cycle = 0
        # Which identifier the measurements endpoint accepts for each device
        # (docs are ambiguous: "deviceId" vs serial number). None = unsupported.
        self._measure_id: dict[str, str | None] = {}
        # Raw payloads kept for diagnostics
        self.raw_devices: list[dict[str, Any]] = []
        self.raw_alarms: dict[str, Any] = {}
        self.raw_measurements: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------

    async def _device_measure(self, inverter: InverterData, func, *args) -> Any:
        """Call a per-device measurement, discovering the right identifier."""
        dev_id = inverter.device_id
        if dev_id in self._measure_id:
            ident = self._measure_id[dev_id]
            if ident is None:
                return None
            return await func(self.plant_id, *args, device_id=ident)

        candidates = [c for c in (inverter.device_id, inverter.serial_number) if c]
        if not candidates:
            return None

        for ident in candidates:
            try:
                result = await func(self.plant_id, *args, device_id=ident)
            except (WegSolarAuthError, *_TRANSIENT):
                raise
            except WegSolarApiError as err:
                if err.status is None or not (400 <= err.status < 500):
                    raise
                _LOGGER.debug(
                    "Measurement with deviceId=%s rejected (%s): %s",
                    ident,
                    err.status,
                    err,
                )
                continue
            self._measure_id[dev_id] = ident
            return result

        _LOGGER.warning(
            "Per-inverter measurements are not available for %s (%s)",
            inverter.name,
            inverter.serial_number,
        )
        self._measure_id[dev_id] = None
        return None

    def _build_inverters(self, devices: list[dict[str, Any]]) -> tuple[dict[str, InverterData], list[dict[str, Any]]]:
        """Split devices into active inverters and replaced ones."""
        previous = self.data.inverters if self.data else {}
        active: dict[str, InverterData] = {}
        replaced: list[dict[str, Any]] = []
        for dev in devices:
            dev_id = dev.get("deviceId") or dev.get("_id") or dev.get("id")
            if not dev_id:
                continue
            if is_replaced(dev):
                replaced.append(dev)
                continue
            old = previous.get(dev_id)
            active[dev_id] = InverterData(
                device_id=dev_id,
                name=dev.get("name") or f"Inverter {dev.get('serialNumber', dev_id)}",
                model=dev.get("productModelId") or dev.get("model"),
                serial_number=dev.get("serialNumber"),
                status=dev.get("status"),
                lifetime_energy=parse_energy_kwh(dev.get("outputEnergy")),
                power=old.power if old else None,
                energy_today=old.energy_today if old else None,
                last_alarm=old.last_alarm if old else None,
                alarms_24h=old.alarms_24h if old else 0,
                raw=dev,
            )
        return active, replaced

    def _apply_alarms(self, plant: PlantData, alarms: dict[str, Any], now: datetime) -> None:
        """Distribute alarms to the plant and its inverters."""
        items = [a for a in alarms.get("items", []) if isinstance(a, dict)]
        items.sort(key=lambda a: a.get("time", ""), reverse=True)
        cutoff = now - timedelta(hours=24)

        def recent(alarm: dict[str, Any]) -> bool:
            ts = dt_util.parse_datetime(alarm.get("time", "") or "")
            return ts is not None and ts >= cutoff

        plant.alarms = items
        plant.alarms_total = int(alarms.get("totalCount", len(items)) or 0)
        plant.alarms_24h = sum(1 for a in items if recent(a))
        plant.last_alarm = items[0] if items else None

        for inv in plant.inverters.values():
            mine = [
                a
                for a in items
                if a.get("deviceId") == inv.device_id
                or (inv.serial_number and a.get("serialNumber") == inv.serial_number)
            ]
            inv.last_alarm = mine[0] if mine else None
            inv.alarms_24h = sum(1 for a in mine if recent(a))

    # ------------------------------------------------------------------

    async def _async_update_data(self) -> PlantData:
        """Fetch data from the API."""
        now = dt_util.utcnow()
        start_of_day = dt_util.start_of_local_day()
        prev = self.data

        try:
            return await self._fetch(now, start_of_day, prev)
        except WegSolarAuthError as err:
            raise ConfigEntryAuthFailed("API credentials are no longer valid") from err
        except WegSolarApiError as err:
            raise UpdateFailed(f"Error fetching plant {self.plant_name}: {err}") from err

    async def _fetch(self, now: datetime, start_of_day: datetime, prev: PlantData | None) -> PlantData:
        # 1. Devices (mandatory on first run)
        try:
            devices = await self.client.async_get_plant_devices(self.plant_id)
            self.raw_devices = devices
        except (WegSolarAuthError,):
            raise
        except WegSolarApiError:
            if prev is None:
                raise
            _LOGGER.debug("Keeping previous device list for %s", self.plant_name)
            devices = self.raw_devices

        inverters, replaced = self._build_inverters(devices)
        plant = PlantData(
            plant_id=self.plant_id,
            name=self.plant_name,
            inverters=inverters,
            replaced_devices=replaced,
            power=prev.power if prev else None,
            energy_today=prev.energy_today if prev else None,
            alarms=prev.alarms if prev else [],
            alarms_total=prev.alarms_total if prev else 0,
            alarms_24h=prev.alarms_24h if prev else 0,
            last_alarm=prev.last_alarm if prev else None,
        )

        # Lifetime energy of the plant includes replaced inverters history.
        lifetimes = [parse_energy_kwh(d.get("outputEnergy")) for d in devices]
        lifetimes = [v for v in lifetimes if v is not None]
        plant.lifetime_energy = round(sum(lifetimes), 3) if lifetimes else None

        raw_measurements: dict[str, dict[str, Any]] = {}

        # 2. Per-inverter measurements (active inverters only)
        for inv in inverters.values():
            try:
                power_res = await self._device_measure(
                    inv, self.client.async_get_current_power, now
                )
                if power_res is not None:
                    inv.power, meta = power_res
                    raw_measurements.setdefault(inv.device_id, {})["power"] = meta
                energy_res = await self._device_measure(
                    inv, self.client.async_get_energy_today, start_of_day, now
                )
                if energy_res is not None:
                    inv.energy_today, meta = energy_res
                    raw_measurements.setdefault(inv.device_id, {})["energy_today"] = meta
            except WegSolarAuthError:
                raise
            except WegSolarApiError as err:
                _LOGGER.debug("Measurements failed for inverter %s: %s", inv.name, err)

        # 3. Plant-level power and energy (derived from active inverters when possible)
        if inverters and all(inv.power is not None for inv in inverters.values()):
            plant.power = round(
                sum(inv.power for inv in inverters.values() if inv.power is not None),
                1,
            )
        else:
            try:
                plant.power, meta = await self.client.async_get_current_power(
                    self.plant_id, now
                )
                raw_measurements.setdefault("plant", {})["power"] = meta
            except WegSolarAuthError:
                raise
            except WegSolarApiError as err:
                _LOGGER.debug(
                    "Plant power measurement failed for %s: %s", self.plant_name, err
                )

        if inverters and all(
            inv.energy_today is not None for inv in inverters.values()
        ):
            plant.energy_today = round(
                sum(
                    inv.energy_today
                    for inv in inverters.values()
                    if inv.energy_today is not None
                ),
                3,
            )
        else:
            try:
                plant.energy_today, meta = await self.client.async_get_energy_today(
                    self.plant_id, start_of_day, now
                )
                raw_measurements.setdefault("plant", {})["energy_today"] = meta
            except WegSolarAuthError:
                raise
            except WegSolarApiError as err:
                _LOGGER.debug(
                    "Plant energy measurement failed for %s: %s", self.plant_name, err
                )

        self.raw_measurements = raw_measurements

        # 4. Alarms (less frequently)
        if prev is None or self._cycle % ALARMS_EVERY_N_CYCLES == 0:
            try:
                self.raw_alarms = await self.client.async_get_plant_alarms(self.plant_id)
            except WegSolarAuthError:
                raise
            except WegSolarApiError as err:
                _LOGGER.debug("Alarms failed for %s: %s", self.plant_name, err)
        self._cycle += 1
        if self.raw_alarms:
            self._apply_alarms(plant, self.raw_alarms, now)

        return plant
