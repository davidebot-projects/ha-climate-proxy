# Climate Proxy for Home Assistant

Control an air conditioner's **Dry mode from Apple Home**, using the
**Auto** position of its existing thermostat controls. Each configured unit
gets one Home Assistant `climate` proxy. Expose that proxy to HomeKit to keep
one accessory per air conditioner.

An optional preset can be applied after every power-on and operating-mode
command through the proxy. The preset field defaults to `eco`; choose a preset
your source supports, or leave it empty to send no automatic preset commands.
The physical unit still regulates temperature and dehumidification.

## Controls

| Apple Home control | Command sent to the source |
| --- | --- |
| Off | Off; no preset command |
| Heat | Heat, then the configured preset, if any |
| Cool | Cool, then the configured preset, if any |
| Auto | **Dry, then the configured preset, if any** |
| Power on | Restore the source's operating mode, then the configured preset, if any |

HomeKit's thermostat and heater/cooler services have no separate Dry position.
The proxy exposes `heat_cool` to HomeKit and translates commands for that slot
into `dry` on the original entity.

**The label remains Auto in Apple Home.** It cannot be renamed to Dry or
Dehumidify. HomePod can select the automatic mode; it does not gain a native
Dry command. The real automatic heating/cooling mode is unavailable through
the proxy, but remains available through the original entity in Home Assistant.

Temperature, fan mode, swing and presets are forwarded to the original entity.
Their availability in Apple Home depends on the bridge's accessory type and
the capabilities exposed by the source. The proxy does not add temperature
control to Dry if the unit does not support it.

## Requirements

- Home Assistant **2025.3.0** or newer.
- Home Assistant **2026.3** or newer to display the bundled integration icon.
- An existing `climate` entity for each air conditioner.
- Source modes **Off, Heat, Cool and Dry**. The proxy assumes these modes are
  available; it does not probe for them.
- If you configure an automatic preset, it must be listed in the source
  entity's `preset_modes`. Leave the field empty if no preset is needed or
  supported.

The integration uses standard Home Assistant climate services and works with
source entities meeting the requirements above.

## Installation

### HACS

1. Add `https://github.com/davidebot-projects/ha-climate-proxy` under
   **HACS → Custom repositories**, category **Integration**.
2. Download **Climate Proxy**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/climate_proxy` from this repository into
`/config/custom_components/climate_proxy`, then restart Home Assistant.

## Configure each unit

Go to **Settings → Devices & services → Add integration → Climate Proxy**.

| Field | Value |
| --- | --- |
| Climate entity | The original climate entity |
| Name | A distinct name, such as `Soggiorno HomeKit` |
| Preset on every power-on and mode change | `eco` by default; choose a supported preset or leave empty |

Repeat for each air conditioner. Each proxy is grouped with the source's
device when the original entity belongs to a registered device. The original
entity remains available in Home Assistant.

Use **Configure** on the integration to change the proxy name or preset.
Preset names must match the source's `preset_modes` values exactly. Validation
runs when the source is available; if it is unavailable during setup, the
preset is checked when a command is executed.

Find the actual proxy entity IDs under **Settings → Devices & services →
Entities**. Names in the example below are illustrative.

## Expose the proxies to HomeKit

Replace the original climate entities in your bridge's `include_entities`
with the proxy entities. Keep your other bridge entities as they are.

For a separate `homekit.yaml` included as a list:

```yaml
- name: HASS Clima
  port: 21063
  mode: bridge
  filter:
    include_entities:
      - climate.riscaldamento_a_pavimento
      - climate.soggiorno_homekit
      - climate.disimpegno_homekit
      - humidifier.aquaria_s1_wi_fi_bluetooth
  entity_config:
    climate.riscaldamento_a_pavimento:
      type: thermostat
    climate.soggiorno_homekit:
      name: Condizionatore Soggiorno
      type: thermostat
    climate.disimpegno_homekit:
      name: Condizionatore Disimpegno
      type: thermostat
```

This selects a Thermostat accessory with Off, Heat, Cool and Auto. You can omit
`type: thermostat` for the air conditioners and let the bridge choose the
accessory type. A Heater Cooler accessory exposes power separately from its
Auto, Heat and Cool mode control; Auto still activates Dry.

Reload the HomeKit integration or restart Home Assistant after changing the
configuration. Because a proxy has a different entity ID from the original,
HomeKit treats it as a new accessory. Check its room, name, and any scenes or
automations that referenced the old accessory.

For a bridge created through the Home Assistant UI, select the proxy entities
in the bridge's options instead of adding a YAML bridge.

## Behavior

- **The optional preset is applied after the mode or power command.** It is
  requested in Dry, Heat and Cool, and is not cleared when leaving Dry. An empty
  preset field disables these automatic preset commands. The preset's behavior
  depends on the source integration and device.
- **Preset overrides are possible.** An explicit preset command is forwarded.
  The configured preset is applied again on the next power-on or operating-mode
  command through the proxy. Changing only temperature, fan or swing does not
  reapply the preset.
- **External controls remain independent.** Commands through the original
  entity or physical remote are reflected by the proxy but do not force the configured preset.
  Source Dry, Auto and Heat/Cool all appear as Auto; source Fan Only appears as
  Cool. Selecting Auto through the proxy always requests Dry.
- **All forwarded commands are serialized.** A simultaneous power or mode
  request cannot interrupt the sequence between setting a mode and its preset.
  Combined temperature/mode commands also translate Auto into Dry.
- **Failures are reported.** If the mode command fails, the preset is not sent.
  If the preset command fails, the preceding mode or power command may already
  have succeeded; the proxy does not roll it back.
- **Source renames are followed.** The stored mapping and state subscription
  are updated while the proxy's entity identity is preserved.
- **Availability follows the source.** Last known attributes are cached during
  outages to preserve capability lists. The proxy remains unavailable until
  the source recovers.
- **A single temperature setpoint is exposed.** Temperature-range support is
  intentionally omitted so Auto does not introduce separate heating and cooling
  thresholds.

## Development checks

From the repository directory:

```sh
python3 -m unittest discover -s tests -v
```

The tests cover mode translation, optional preset commands, command ordering,
failures, source renames, and recovery from unavailable states.

## License

MIT. See [LICENSE](LICENSE).
