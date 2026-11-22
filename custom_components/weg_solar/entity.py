"""Base entities for WEG Solar Portal."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, PORTAL_URL
from .coordinator import InverterData, WegSolarConfigEntry, WegSolarCoordinator


class WegSolarPlantEntity(CoordinatorEntity[WegSolarCoordinator]):
    """Entity attached to the plant device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: WegSolarCoordinator, description: EntityDescription
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.plant_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.plant_id)},
            name=coordinator.plant_name,
            manufacturer=MANUFACTURER,
            model="Solar Plant",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=PORTAL_URL,
        )


class WegSolarInverterEntity(CoordinatorEntity[WegSolarCoordinator]):
    """Entity attached to an inverter device (child of the plant)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WegSolarCoordinator,
        inverter: InverterData,
        description: EntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = inverter.device_id
        self._attr_unique_id = f"{inverter.device_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inverter.device_id)},
            name=inverter.name,
            manufacturer=MANUFACTURER,
            model=inverter.model,
            serial_number=inverter.serial_number,
            via_device=(DOMAIN, coordinator.plant_id),
            configuration_url=PORTAL_URL,
        )

    @property
    def inverter(self) -> InverterData | None:
        """Return the current inverter data."""
        return self.coordinator.data.inverters.get(self._device_id)

    @property
    def available(self) -> bool:
        """Unavailable if the inverter disappeared / was replaced."""
        return super().available and self.inverter is not None


def async_setup_inverter_entities(
    entry: WegSolarConfigEntry,
    add_entities: Callable[[Iterable[Entity]], None],
    factory: Callable[[InverterData], Iterable[Entity]],
) -> None:
    """Add inverter entities now and whenever a new inverter shows up."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _check() -> None:
        new = [
            inv for dev_id, inv in coordinator.data.inverters.items() if dev_id not in known
        ]
        if not new:
            return
        known.update(inv.device_id for inv in new)
        add_entities([e for inv in new for e in factory(inv)])

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))
