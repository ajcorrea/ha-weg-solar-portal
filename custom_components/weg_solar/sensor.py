"""Sensor platform for WEG Solar Portal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import InverterData, PlantData, WegSolarConfigEntry
from .entity import (
    WegSolarInverterEntity,
    WegSolarPlantEntity,
    async_setup_inverter_entities,
)


def _alarm_time(alarm: dict[str, Any] | None) -> datetime | None:
    if not alarm:
        return None
    return dt_util.parse_datetime(alarm.get("time") or "")


def _alarm_attrs(alarm: dict[str, Any] | None) -> dict[str, Any] | None:
    if not alarm:
        return None
    return {
        "code": alarm.get("code"),
        "severity": alarm.get("severity"),
        "description": alarm.get("description"),
        "alarm_status": alarm.get("status"),
        "model": alarm.get("model"),
        "serial_number": alarm.get("serialNumber"),
    }


@dataclass(frozen=True, kw_only=True)
class PlantSensorDescription(SensorEntityDescription):
    """Plant sensor description."""

    value_fn: Callable[[PlantData], Any]
    attrs_fn: Callable[[PlantData], dict[str, Any] | None] = lambda _: None


@dataclass(frozen=True, kw_only=True)
class InverterSensorDescription(SensorEntityDescription):
    """Inverter sensor description."""

    value_fn: Callable[[InverterData], Any]
    attrs_fn: Callable[[InverterData], dict[str, Any] | None] = lambda _: None


PLANT_SENSORS: tuple[PlantSensorDescription, ...] = (
    PlantSensorDescription(
        key="current_power",
        translation_key="current_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda p: p.power,
    ),
    PlantSensorDescription(
        key="today_energy",
        translation_key="today_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda p: p.energy_today,
    ),
    PlantSensorDescription(
        key="lifetime_energy",
        translation_key="lifetime_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda p: p.lifetime_energy,
    ),
    PlantSensorDescription(
        key="inverters_online",
        translation_key="inverters_online",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.inverters_online,
        attrs_fn=lambda p: {
            "inverters_total": len(p.inverters),
            "inverters": [
                {"name": i.name, "serial_number": i.serial_number, "status": i.status}
                for i in p.inverters.values()
            ],
            "replaced_inverters": [
                {
                    "name": d.get("name"),
                    "serial_number": d.get("serialNumber"),
                    "model": d.get("productModelId") or d.get("model"),
                    "output_energy": d.get("outputEnergy"),
                    "replaced_by": d.get("replacement"),
                }
                for d in p.replaced_devices
            ],
        },
    ),
    PlantSensorDescription(
        key="alarms_24h",
        translation_key="alarms_24h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.alarms_24h,
        attrs_fn=lambda p: {
            "alarms_total": p.alarms_total,
            "recent_alarms": [
                {"time": a.get("time"), **(_alarm_attrs(a) or {})} for a in p.alarms[:5]
            ],
        },
    ),
    PlantSensorDescription(
        key="last_alarm",
        translation_key="last_alarm",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: _alarm_time(p.last_alarm),
        attrs_fn=lambda p: _alarm_attrs(p.last_alarm),
    ),
)

INVERTER_SENSORS: tuple[InverterSensorDescription, ...] = (
    InverterSensorDescription(
        key="current_power",
        translation_key="current_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda i: i.power,
    ),
    InverterSensorDescription(
        key="today_energy",
        translation_key="today_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda i: i.energy_today,
    ),
    InverterSensorDescription(
        key="lifetime_energy",
        translation_key="lifetime_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda i: i.lifetime_energy,
        attrs_fn=lambda i: {"portal_value": i.raw.get("outputEnergy")},
    ),
    InverterSensorDescription(
        key="device_status",
        translation_key="device_status",
        value_fn=lambda i: i.status,
    ),
    InverterSensorDescription(
        key="last_alarm",
        translation_key="last_alarm",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda i: _alarm_time(i.last_alarm),
        attrs_fn=lambda i: _alarm_attrs(i.last_alarm),
    ),
    InverterSensorDescription(
        key="alarms_24h",
        translation_key="alarms_24h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda i: i.alarms_24h,
    ),
    InverterSensorDescription(
        key="serial_number",
        translation_key="serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda i: i.serial_number,
    ),
    InverterSensorDescription(
        key="model",
        translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda i: i.model,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WegSolarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for one plant and its inverters."""
    coordinator = entry.runtime_data
    async_add_entities(WegSolarPlantSensor(coordinator, d) for d in PLANT_SENSORS)
    async_setup_inverter_entities(
        entry,
        async_add_entities,
        lambda inv: [WegSolarInverterSensor(coordinator, inv, d) for d in INVERTER_SENSORS],
    )


class WegSolarPlantSensor(WegSolarPlantEntity, SensorEntity):
    """Sensor of a plant."""

    entity_description: PlantSensorDescription

    @property
    def native_value(self) -> Any:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        return self.entity_description.attrs_fn(self.coordinator.data)


class WegSolarInverterSensor(WegSolarInverterEntity, SensorEntity):
    """Sensor of an inverter."""

    entity_description: InverterSensorDescription

    @property
    def native_value(self) -> Any:
        """Return the state."""
        inv = self.inverter
        return self.entity_description.value_fn(inv) if inv else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        inv = self.inverter
        return self.entity_description.attrs_fn(inv) if inv else None
