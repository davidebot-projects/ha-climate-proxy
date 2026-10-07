"""Config and options flow for Climate Proxy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.climate import ATTR_PRESET_MODES, DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
)

from .const import (
    CONF_AUTO_PRESET,
    CONF_SOURCE_ENTITY,
    DEFAULT_AUTO_PRESET,
    DOMAIN,
)


def _presets(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Return the presets the source offers, if it is loaded."""
    if (state := hass.states.get(entity_id)) is None:
        return []
    return list(state.attributes.get(ATTR_PRESET_MODES) or [])


def _schema(defaults: Mapping[str, Any], *, with_source: bool) -> vol.Schema:
    """Build the form, hiding the source once it has been chosen."""
    fields: dict[Any, Any] = {}

    if with_source:
        fields[vol.Required(CONF_SOURCE_ENTITY)] = EntitySelector(
            EntitySelectorConfig(domain=CLIMATE_DOMAIN)
        )

    fields[
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, ""))
    ] = TextSelector()
    fields[
        vol.Optional(
            CONF_AUTO_PRESET,
            default=defaults.get(CONF_AUTO_PRESET, DEFAULT_AUTO_PRESET),
        )
    ] = TextSelector()

    return vol.Schema(fields)


def _validate(
    hass: HomeAssistant, source: str, user_input: dict[str, Any]
) -> dict[str, str]:
    """Reject a preset the source does not actually offer."""
    preset = (user_input.get(CONF_AUTO_PRESET) or "").strip()
    state = hass.states.get(source)
    if (
        preset
        and state is not None
        and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        and preset not in _presets(hass, source)
    ):
        return {CONF_AUTO_PRESET: "unknown_preset"}
    return {}


class ClimateProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Climate Proxy config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ClimateProxyOptionsFlow:
        """Return the options flow handler."""
        return ClimateProxyOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick the climate entity to wrap."""
        errors: dict[str, str] = {}

        if user_input is not None:
            source = user_input[CONF_SOURCE_ENTITY]
            if not (errors := _validate(self.hass, source, user_input)):
                await self.async_set_unique_id(source)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={},
                    options={
                        CONF_SOURCE_ENTITY: source,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_AUTO_PRESET: (
                            user_input.get(CONF_AUTO_PRESET) or ""
                        ).strip(),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}, with_source=True),
            errors=errors,
        )


class ClimateProxyOptionsFlow(config_entries.OptionsFlow):
    """Handle Climate Proxy reconfiguration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user correct the name or the operating preset."""
        options = self.config_entry.options
        source: str = options[CONF_SOURCE_ENTITY]
        errors: dict[str, str] = {}

        if user_input is not None:
            if not (errors := _validate(self.hass, source, user_input)):
                return self.async_create_entry(
                    data={
                        CONF_SOURCE_ENTITY: source,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_AUTO_PRESET: (
                            user_input.get(CONF_AUTO_PRESET) or ""
                        ).strip(),
                    }
                )

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(user_input or options, with_source=False),
            errors=errors,
            description_placeholders={"source": source},
        )
