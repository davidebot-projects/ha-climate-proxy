"""Climate platform for Climate Proxy.

HomeKit's thermostat services have no Dry position: `TargetHeaterCoolerState`
offers Auto, Heat and Cool, and Home Assistant drops `dry` outright when the
entity also exposes `cool`. That mapping, however, is built from the entity's
own `hvac_modes` - so an entity that presents `heat_cool` where the real device
does `dry` puts dehumidification behind the Auto position of the thermostat
tile, without adding a single accessory.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    ATTR_SWING_MODE,
    ATTR_SWING_MODES,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    CONF_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_AUTO_PRESET, CONF_SOURCE_ENTITY, DEFAULT_AUTO_PRESET

_LOGGER = logging.getLogger(__name__)

INVALID_STATES = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE})

# What the proxy reports for each mode the source can be in. Everything the
# thermostat tile cannot show is folded onto the nearest slot it can.
SOURCE_TO_PROXY: dict[HVACMode, HVACMode] = {
    HVACMode.OFF: HVACMode.OFF,
    HVACMode.HEAT: HVACMode.HEAT,
    HVACMode.COOL: HVACMode.COOL,
    HVACMode.DRY: HVACMode.HEAT_COOL,
    HVACMode.HEAT_COOL: HVACMode.HEAT_COOL,
    HVACMode.AUTO: HVACMode.HEAT_COOL,
    HVACMode.FAN_ONLY: HVACMode.COOL,
}

# What the source is asked to do for each mode the proxy exposes.
PROXY_TO_SOURCE: dict[HVACMode, HVACMode] = {
    HVACMode.OFF: HVACMode.OFF,
    HVACMode.HEAT: HVACMode.HEAT,
    HVACMode.COOL: HVACMode.COOL,
    HVACMode.HEAT_COOL: HVACMode.DRY,
}

# Mirrored from the source when it has them. TARGET_TEMPERATURE_RANGE is
# deliberately excluded: without it Home Assistant keeps `target_temp_low` and
# `target_temp_high` out of the state attributes, and the HomeKit accessory
# then drives a single setpoint even while the Auto slot is selected.
MIRRORED_FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.PRESET_MODE
    | ClimateEntityFeature.SWING_MODE
    | ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TURN_OFF
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Climate Proxy entity."""
    async_add_entities([ClimateProxyEntity(hass, entry)])


class ClimateProxyEntity(ClimateEntity):
    """Present a climate entity with Dry folded onto the Auto slot."""

    _attr_should_poll = False
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.HEAT_COOL,
    ]

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the proxy and attach it to the source device."""
        self._source: str = entry.options[CONF_SOURCE_ENTITY]
        # Keep the existing option key so installed helpers retain their settings.
        self._operating_preset: str = entry.options.get(
            CONF_AUTO_PRESET, DEFAULT_AUTO_PRESET
        ) or ""
        self._command_lock = asyncio.Lock()
        self._last_attributes: dict[str, Any] = {}
        self._remember_attributes(hass.states.get(self._source))

        self._attr_name = entry.options[CONF_NAME]
        self._attr_unique_id = f"{entry.entry_id}_climate"

        # Assigning `device_entry` groups the proxy with the real unit without
        # adding this config entry to that device, the pattern core helpers use.
        registry_entry = er.async_get(hass).async_get(self._source)
        if registry_entry and registry_entry.device_id:
            if device := dr.async_get(hass).async_get(registry_entry.device_id):
                self.device_entry = device

    # -- Source tracking ----------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Subscribe to the source entity."""
        await super().async_added_to_hass()
        self._remember_attributes(self.hass.states.get(self._source))

        if self.hass.states.get(self._source) is None:
            _LOGGER.warning(
                "%s: source entity %s does not exist; the proxy stays "
                "unavailable until it appears",
                self.entity_id,
                self._source,
            )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source], self._async_source_changed
            )
        )

    @callback
    def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        """Refresh when the source changes."""
        self._remember_attributes(event.data.get("new_state"))
        self.async_write_ha_state()

    def _remember_attributes(self, state: State | None) -> None:
        """Retain the last usable attributes across outages and removal."""
        if state is not None and state.state not in INVALID_STATES:
            self._last_attributes = dict(state.attributes)

    @property
    def _state(self) -> State | None:
        """Return a usable source state, or None when unknown/unavailable."""
        state = self.hass.states.get(self._source)
        if state is None or state.state in INVALID_STATES:
            return None
        return state

    def _attribute(self, name: str, fallback: Any = None) -> Any:
        """Read an attribute of the source, even while it is unavailable.

        Capability attributes such as the fan mode list must survive a
        momentary outage, or the entity would keep losing and regaining
        features.
        """
        state = self._state
        attributes = state.attributes if state is not None else self._last_attributes
        value = attributes.get(name)
        return fallback if value is None else value

    # -- Availability -------------------------------------------------------

    @property
    def available(self) -> bool:
        """Return whether the source is usable."""
        return self._state is not None

    # -- State --------------------------------------------------------------

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Mirror the source's features, minus the ones the proxy reshapes."""
        features = ClimateEntityFeature(
            int(self._attribute(ATTR_SUPPORTED_FEATURES, 0))
        )
        return features & MIRRORED_FEATURES

    @property
    def temperature_unit(self) -> str:
        """Return the unit of the source, defaulting to the system one."""
        return self._attribute(
            "temperature_unit",
            self.hass.config.units.temperature_unit or UnitOfTemperature.CELSIUS,
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the source's mode, translated onto the exposed slots."""
        if (state := self._state) is None:
            return None
        try:
            source_mode = HVACMode(state.state)
        except ValueError:
            return None
        return SOURCE_TO_PROXY.get(source_mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return what the unit is doing, untouched."""
        try:
            return HVACAction(self._attribute(ATTR_HVAC_ACTION))
        except ValueError:
            return None

    @property
    def current_temperature(self) -> float | None:
        """Return the measured temperature."""
        return self._attribute(ATTR_CURRENT_TEMPERATURE)

    @property
    def target_temperature(self) -> float | None:
        """Return the setpoint."""
        return self._attribute(ATTR_TEMPERATURE)

    @property
    def min_temp(self) -> float:
        """Return the lowest settable temperature."""
        return float(self._attribute(ATTR_MIN_TEMP, super().min_temp))

    @property
    def max_temp(self) -> float:
        """Return the highest settable temperature."""
        return float(self._attribute(ATTR_MAX_TEMP, super().max_temp))

    @property
    def target_temperature_step(self) -> float | None:
        """Return the setpoint step."""
        return self._attribute(ATTR_TARGET_TEMP_STEP)

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        return self._attribute(ATTR_FAN_MODE)

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the selectable fan modes."""
        return self._attribute(ATTR_FAN_MODES)

    @property
    def swing_mode(self) -> str | None:
        """Return the current swing mode."""
        return self._attribute(ATTR_SWING_MODE)

    @property
    def swing_modes(self) -> list[str] | None:
        """Return the selectable swing modes."""
        return self._attribute(ATTR_SWING_MODES)

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset."""
        return self._attribute(ATTR_PRESET_MODE)

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the selectable presets."""
        return self._attribute(ATTR_PRESET_MODES)

    # -- Commands -----------------------------------------------------------

    async def _async_call(self, service: str, data: dict[str, Any]) -> None:
        """Call a climate service on the source."""
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            service,
            {ATTR_ENTITY_ID: self._source, **data},
            blocking=True,
        )

    async def _async_apply_operating_preset(self) -> None:
        """Apply the configured preset after every power-on or mode change."""
        preset = self._operating_preset
        if not preset:
            return
        if preset not in (self.preset_modes or ()):
            raise ServiceValidationError(
                f"{self._source} does not offer the configured preset {preset!r}"
            )
        await self._async_call(SERVICE_SET_PRESET_MODE, {ATTR_PRESET_MODE: preset})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Translate the requested slot into a mode the source understands."""
        if (source_mode := PROXY_TO_SOURCE.get(hvac_mode)) is None:
            raise ServiceValidationError(f"Unsupported proxy HVAC mode: {hvac_mode}")

        async with self._command_lock:
            await self._async_set_mode(source_mode)

    async def _async_set_mode(self, source_mode: HVACMode) -> None:
        """Set a source mode and its preset while holding the command lock."""
        await self._async_call(SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: source_mode})
        if source_mode != HVACMode.OFF:
            await self._async_apply_operating_preset()

    async def async_turn_on(self) -> None:
        """Forward the power on, letting the source pick the mode it restores."""
        async with self._command_lock:
            await self._async_call(SERVICE_TURN_ON, {})
            await self._async_apply_operating_preset()

    async def async_turn_off(self) -> None:
        """Forward the power off."""
        async with self._command_lock:
            await self._async_call(SERVICE_TURN_OFF, {})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Forward the setpoint, ignoring any range the caller sent."""
        async with self._command_lock:
            if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
                if (source_mode := PROXY_TO_SOURCE.get(hvac_mode)) is None:
                    raise ServiceValidationError(
                        f"Unsupported proxy HVAC mode: {hvac_mode}"
                    )
                await self._async_set_mode(source_mode)
            if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
                await self._async_call(
                    SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: temperature}
                )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Forward the fan mode."""
        async with self._command_lock:
            await self._async_call(SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: fan_mode})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Forward the swing mode."""
        async with self._command_lock:
            await self._async_call(SERVICE_SET_SWING_MODE, {ATTR_SWING_MODE: swing_mode})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Forward the preset."""
        async with self._command_lock:
            await self._async_call(
                SERVICE_SET_PRESET_MODE, {ATTR_PRESET_MODE: preset_mode}
            )
