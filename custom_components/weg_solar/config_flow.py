"""Config flow for WEG Solar Portal integration (one entry per plant)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    WegSolarApiError,
    WegSolarAuthError,
    WegSolarConnectionError,
    WegSolarRateLimitError,
)
from .const import CONF_API_KEY, CONF_API_SECRET, CONF_PLANT_ID, CONF_PLANT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(),
        vol.Required(CONF_API_SECRET): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class WegSolarConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for WEG Solar Portal."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._credentials: dict[str, str] = {}
        self._plants: dict[str, str] = {}
        self._discovered: dict[str, str] = {}

    async def _async_fetch_plants(
        self, api_key: str, api_secret: str
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Validate credentials and return (plants, error_key)."""
        from . import get_client  # avoid import cycle at module load

        client = get_client(self.hass, api_key, api_secret)
        try:
            return await client.async_get_plants(), None
        except WegSolarAuthError:
            return None, "invalid_auth"
        except WegSolarRateLimitError:
            return None, "rate_limited"
        except WegSolarConnectionError:
            return None, "cannot_connect"
        except WegSolarApiError:
            return None, "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            return None, "unknown"

    # ------------------------------------------------------------------
    # User flow: credentials -> plant selection
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            plants, error = await self._async_fetch_plants(
                user_input[CONF_API_KEY], user_input[CONF_API_SECRET]
            )
            if error:
                errors["base"] = error
            else:
                configured = self._async_current_ids(include_ignore=False)
                self._plants = {
                    p["_id"]: p.get("name") or p["_id"]
                    for p in plants or []
                    if p.get("_id") and p["_id"] not in configured
                }
                if not plants:
                    return self.async_abort(reason="no_plants")
                if not self._plants:
                    return self.async_abort(reason="already_configured")
                self._credentials = user_input
                return await self.async_step_plant()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_plant(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which plant to add."""
        if user_input is not None or len(self._plants) == 1:
            plant_id = (
                user_input[CONF_PLANT_ID] if user_input else next(iter(self._plants))
            )
            return await self._async_create_plant_entry(
                plant_id, self._plants[plant_id], self._credentials
            )

        return self.async_show_form(
            step_id="plant",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PLANT_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=pid, label=name)
                                for pid, name in self._plants.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def _async_create_plant_entry(
        self, plant_id: str, plant_name: str, credentials: Mapping[str, str]
    ) -> ConfigFlowResult:
        """Create the entry for one plant."""
        await self.async_set_unique_id(plant_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=plant_name,
            data={
                CONF_API_KEY: credentials[CONF_API_KEY],
                CONF_API_SECRET: credentials[CONF_API_SECRET],
                CONF_PLANT_ID: plant_id,
                CONF_PLANT_NAME: plant_name,
            },
        )

    # ------------------------------------------------------------------
    # Discovery of the other plants of an already configured API key
    # ------------------------------------------------------------------

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle a plant discovered from an existing API key."""
        await self.async_set_unique_id(discovery_info[CONF_PLANT_ID])
        self._abort_if_unique_id_configured()
        self._discovered = dict(discovery_info)
        self.context["title_placeholders"] = {"name": discovery_info[CONF_PLANT_NAME]}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered plant."""
        if user_input is not None:
            return await self._async_create_plant_entry(
                self._discovered[CONF_PLANT_ID],
                self._discovered[CONF_PLANT_NAME],
                self._discovered,
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": self._discovered[CONF_PLANT_NAME]},
        )

    # ------------------------------------------------------------------
    # Re-authentication
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            plants, error = await self._async_fetch_plants(
                user_input[CONF_API_KEY], user_input[CONF_API_SECRET]
            )
            if error:
                errors["base"] = error
            elif not any(p.get("_id") == entry.data[CONF_PLANT_ID] for p in plants or []):
                errors["base"] = "plant_not_linked"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=CREDENTIALS_SCHEMA,
            description_placeholders={"name": entry.title},
            errors=errors,
        )
