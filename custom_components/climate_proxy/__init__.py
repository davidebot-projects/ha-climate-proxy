"""Climate Proxy integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_SOURCE_ENTITY

PLATFORMS = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Climate Proxy from a config entry."""
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    entry.async_on_unload(_async_track_source_renames(hass, entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Climate Proxy config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so a new mapping takes effect immediately."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_track_source_renames(
    hass: HomeAssistant, entry: ConfigEntry
) -> CALLBACK_TYPE:
    """Follow source renames without changing the proxy's unique ID."""

    @callback
    def _handle(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        data = event.data
        if (
            data["action"] == "update"
            and data.get("old_entity_id") == entry.options[CONF_SOURCE_ENTITY]
        ):
            options = {**entry.options, CONF_SOURCE_ENTITY: data["entity_id"]}
            # Updating options triggers a reload and rebuilds the subscription.
            hass.config_entries.async_update_entry(
                entry, options=options, unique_id=data["entity_id"]
            )

    return hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _handle)
