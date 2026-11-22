"""Binary sensor platform for WEG Solar Portal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import InverterData, PlantData, WegSolarConfigEntry
from .entity import (
    WegSolarInverterEntity,
    WegSolarPlantEntity,
    async_setup_inverter_entities,
)


@dataclass(frozen=True, kw_only=True)
class PlantBinaryDescription(BinarySensorEntityDescription):
    """Plant binary sensor description."""

    value_fn: Callable[[PlantData], bool | None]


@dataclass(frozen=True, kw_only=True)
class InverterBinaryDescription(BinarySensorEntityDescription):
    """Inverter binary sensor description."""

    value_fn: Callable[[InverterData], bool | None]


PLANT_BINARY_SENSORS: tuple[PlantBinaryDescription, ...] = (
    PlantBinaryDescription(
        key="has_alarms",
        translation_key="has_alarms",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda p: p.alarms_24h > 0,
    ),
    PlantBinaryDescription(
        key="all_inverters_online",
        translation_key="all_inverters_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda p: bool(p.inverters) and p.inverters_online == len(p.inverters),
    ),
)

INVERTER_BINARY_SENSORS: tuple[InverterBinaryDescription, ...] = (
    InverterBinaryDescription(
        key="device_online",
        translation_key="device_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda i: i.online,
    ),
    InverterBinaryDescription(
        key="has_alarms",
        translation_key="has_alarms",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda i: i.alarms_24h > 0,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WegSolarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for one plant and its inverters."""
    coordinator = entry.runtime_data
    async_add_entities(
        WegSolarPlantBinarySensor(coordinator, d) for d in PLANT_BINARY_SENSORS
    )
    async_setup_inverter_entities(
        entry,
        async_add_entities,
        lambda inv: [
            WegSolarInverterBinarySensor(coordinator, inv, d)
            for d in INVERTER_BINARY_SENSORS
        ],
    )


class WegSolarPlantBinarySensor(WegSolarPlantEntity, BinarySensorEntity):
    """Binary sensor of a plant."""

    entity_description: PlantBinaryDescription

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)


class WegSolarInverterBinarySensor(WegSolarInverterEntity, BinarySensorEntity):
    """Binary sensor of an inverter."""

    entity_description: InverterBinaryDescription

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        inv = self.inverter
        return self.entity_description.value_fn(inv) if inv else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the raw status reported by the portal."""
        inv = self.inverter
        return {"portal_status": inv.status} if inv else None
