"""Button platform for WEG Solar Portal."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_API_KEY, CONF_API_SECRET
from .coordinator import WegSolarConfigEntry
from .entity import WegSolarPlantEntity

SCAN_BUTTON = ButtonEntityDescription(
    key="scan_devices",
    translation_key="scan_devices",
    entity_category=EntityCategory.CONFIG,
    icon="mdi:refresh",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WegSolarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons for one plant."""
    async_add_entities([WegSolarScanButton(entry.runtime_data, SCAN_BUTTON)])


class WegSolarScanButton(WegSolarPlantEntity, ButtonEntity):
    """Button to scan immediately for new plants and inverters."""

    async def async_press(self) -> None:
        """Discover new plants for this API key and refresh this plant's devices."""
        from . import async_discover_other_plants

        entry = self.coordinator.config_entry
        await async_discover_other_plants(
            self.hass,
            entry.data[CONF_API_KEY],
            entry.data[CONF_API_SECRET],
            self.coordinator.client,
        )
        await self.coordinator.async_request_refresh()
