"""Behavior tests with simulated HA state/services; no running HA required."""
import ast
import asyncio
from enum import IntFlag, Enum
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'custom_components/climate_proxy'


class HVACMode(str, Enum):
    OFF = 'off'
    HEAT = 'heat'
    COOL = 'cool'
    HEAT_COOL = 'heat_cool'
    AUTO = 'auto'
    DRY = 'dry'
    FAN_ONLY = 'fan_only'


class HVACAction(str, Enum):
    OFF = 'off'
    HEATING = 'heating'
    COOLING = 'cooling'
    DRYING = 'drying'


class Features(IntFlag):
    TARGET_TEMPERATURE = 1
    FAN_MODE = 8
    PRESET_MODE = 16
    SWING_MODE = 32
    TURN_ON = 128
    TURN_OFF = 256


class Entity:
    async def async_added_to_hass(self):
        pass

    def async_on_remove(self, callback):
        self.removers.append(callback)

    def async_write_ha_state(self):
        self.writes += 1


class ServiceValidationError(Exception):
    pass


def load_logic(filename, names, env):
    """Compile production definitions against small HA test doubles."""
    source = ast.parse((ROOT / filename).read_text())
    selected = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    for node in source.body:
        name = getattr(node, 'name', None)
        if isinstance(node, ast.Assign):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign):
            name = node.target.id
        if name in names:
            selected.append(node)
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), filename, 'exec'), env)


def environment():
    env = dict(asyncio=asyncio, HVACMode=HVACMode, HVACAction=HVACAction,
               ClimateEntityFeature=Features, ClimateEntity=Entity,
               ServiceValidationError=ServiceValidationError,
               STATE_UNAVAILABLE='unavailable', STATE_UNKNOWN='unknown',
               callback=lambda f: f, Any=object, State=object, HomeAssistant=object,
               ConfigEntry=object, Event=object, EventStateChangedData=object,
               CALLBACK_TYPE=object, DEFAULT_AUTO_PRESET='eco', CLIMATE_DOMAIN='climate')
    env['UnitOfTemperature'] = SimpleNamespace(CELSIUS='°C')
    tree = ast.parse((ROOT / 'climate.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.name
                if name.startswith(('ATTR_', 'CONF_', 'SERVICE_')):
                    env[name] = name.split('_', 1)[1].lower()
    env['er'] = SimpleNamespace(async_get=lambda hass: SimpleNamespace(async_get=lambda _: None),
                                EVENT_ENTITY_REGISTRY_UPDATED='registry_updated')
    load_logic('climate.py', {'INVALID_STATES', 'SOURCE_TO_PROXY', 'PROXY_TO_SOURCE',
                              'MIRRORED_FEATURES', 'ClimateProxyEntity'}, env)
    return env


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = environment()
        self.state = SimpleNamespace(state='off', attributes={
            'preset_modes': ['none', 'eco', 'boost'], 'preset_mode': 'none',
            'fan_modes': ['Auto', 'Low'], 'supported_features': int(Features.PRESET_MODE),
        })
        self.states = {'climate.source': self.state}
        self.calls = []

        async def service(domain, name, data, blocking):
            self.calls.append((name, dict(data)))
            await asyncio.sleep(0)
            if name == 'set_hvac_mode':
                self.state.state = data['hvac_mode']
            elif name == 'turn_on':
                self.state.state = 'cool'
            elif name == 'turn_off':
                self.state.state = 'off'
            elif name == 'set_preset_mode':
                self.state.attributes['preset_mode'] = data['preset_mode']

        self.hass = SimpleNamespace(
            states=self.states, services=SimpleNamespace(async_call=service),
            config=SimpleNamespace(units=SimpleNamespace(temperature_unit='°C')),
        )
        self.entry = SimpleNamespace(options={'source_entity': 'climate.source', 'name': 'Proxy'},
                                     entry_id='entry')
        self.proxy = self.env['ClimateProxyEntity'](self.hass, self.entry)
        self.proxy.hass = self.hass
        self.proxy.writes = 0
        self.proxy.removers = []

    async def test_all_operating_modes_apply_configured_preset_after_mode(self):
        for mode, expected in ((HVACMode.HEAT_COOL, 'dry'), (HVACMode.HEAT, 'heat'),
                               (HVACMode.COOL, 'cool')):
            self.calls.clear()
            await self.proxy.async_set_hvac_mode(mode)
            self.assertEqual([(name, data.get('hvac_mode', data.get('preset_mode')))
                              for name, data in self.calls],
                             [('set_hvac_mode', expected), ('set_preset_mode', 'eco')])

    async def test_off_does_not_write_preset(self):
        await self.proxy.async_set_hvac_mode(HVACMode.OFF)
        await self.proxy.async_turn_off()
        self.assertEqual([name for name, _ in self.calls], ['set_hvac_mode', 'turn_off'])

    async def test_power_on_applies_configured_preset(self):
        await self.proxy.async_turn_on()
        self.assertEqual([name for name, _ in self.calls], ['turn_on', 'set_preset_mode'])

    async def test_combined_temperature_mode_translates_auto(self):
        await self.proxy.async_set_temperature(hvac_mode='heat_cool', temperature=23)
        self.assertEqual([name for name, _ in self.calls],
                         ['set_hvac_mode', 'set_preset_mode', 'set_temperature'])
        self.assertEqual(self.calls[0][1]['hvac_mode'], 'dry')
        self.assertEqual(self.calls[-1][1]['temperature'], 23)

    async def test_concurrent_mode_requests_do_not_interleave(self):
        await asyncio.gather(self.proxy.async_set_hvac_mode(HVACMode.HEAT_COOL),
                             self.proxy.async_set_hvac_mode(HVACMode.COOL))
        self.assertEqual([name for name, _ in self.calls],
                         ['set_hvac_mode', 'set_preset_mode'] * 2)
        self.assertEqual(self.state.state, 'cool')

    async def test_fan_and_swing_wait_for_mode_and_preset(self):
        await asyncio.gather(self.proxy.async_set_hvac_mode(HVACMode.HEAT_COOL),
                             self.proxy.async_set_fan_mode('Low'),
                             self.proxy.async_set_swing_mode('Off'))
        self.assertEqual([name for name, _ in self.calls],
                         ['set_hvac_mode', 'set_preset_mode',
                          'set_fan_mode', 'set_swing_mode'])

    async def test_temperature_unit_follows_source(self):
        self.assertEqual(self.proxy.temperature_unit, '°C')
        self.state.attributes['temperature_unit'] = '°F'
        self.assertEqual(self.proxy.temperature_unit, '°F')

    async def test_preset_failure_leaves_successful_mode_visible(self):
        original = self.hass.services.async_call

        async def fail_preset(domain, name, data, blocking):
            if name == 'set_preset_mode':
                raise RuntimeError('preset rejected')
            await original(domain, name, data, blocking)

        self.hass.services.async_call = fail_preset
        with self.assertRaises(RuntimeError):
            await self.proxy.async_set_hvac_mode(HVACMode.HEAT_COOL)
        self.assertEqual(self.state.state, 'dry')
        self.assertEqual(self.proxy.hvac_mode, HVACMode.HEAT_COOL)

    async def test_explicit_empty_preset_disables_automatic_preset(self):
        self.entry.options['auto_preset'] = ''
        proxy = self.env['ClimateProxyEntity'](self.hass, self.entry)
        proxy.hass = self.hass
        await proxy.async_turn_on()
        self.assertEqual([name for name, _ in self.calls], ['turn_on'])

    async def test_missing_preset_reports_failure(self):
        self.state.attributes['preset_modes'] = ['none']
        with self.assertRaises(ServiceValidationError):
            await self.proxy.async_turn_on()

    async def test_source_service_failure_does_not_apply_preset(self):
        async def fail(*args, **kwargs):
            raise RuntimeError('device offline')
        self.hass.services.async_call = fail
        with self.assertRaises(RuntimeError):
            await self.proxy.async_set_hvac_mode(HVACMode.HEAT_COOL)
        self.assertEqual(self.calls, [])

    async def test_capabilities_survive_unavailable_and_removal(self):
        self.proxy._remember_attributes(self.state)
        self.states['climate.source'] = SimpleNamespace(state='unavailable', attributes={})
        self.proxy._async_source_changed(SimpleNamespace(data={'new_state': self.states['climate.source']}))
        self.assertFalse(self.proxy.available)
        self.assertEqual(self.proxy.preset_modes, ['none', 'eco', 'boost'])
        self.assertEqual(self.proxy.fan_modes, ['Auto', 'Low'])
        self.states.clear()
        self.proxy._async_source_changed(SimpleNamespace(data={'new_state': None}))
        self.assertEqual(self.proxy.supported_features, Features.PRESET_MODE)
        self.assertIsNone(self.proxy.hvac_mode)

    async def test_recovery_updates_capabilities(self):
        self.state.attributes['fan_modes'] = ['Auto', 'High']
        self.proxy._async_source_changed(SimpleNamespace(data={'new_state': self.state}))
        self.assertTrue(self.proxy.available)
        self.assertEqual(self.proxy.fan_modes, ['Auto', 'High'])

    async def test_rename_updates_mapping_and_config_identity(self):
        # Registry data annotation is postponed in production.
        tree = ast.parse((ROOT / '__init__.py').read_text())
        function = next(node for node in tree.body if getattr(node, 'name', '') == '_async_track_source_renames')
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), function], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), '__init__.py', 'exec'), self.env)
        updates = []
        self.hass.bus = SimpleNamespace(async_listen=lambda name, handler: handler)
        self.hass.config_entries = SimpleNamespace(async_update_entry=lambda entry, **kwargs: updates.append(kwargs))
        handler = self.env['_async_track_source_renames'](self.hass, self.entry)
        handler(SimpleNamespace(data={'action': 'update', 'old_entity_id': 'climate.other', 'entity_id': 'climate.new'}))
        self.assertEqual(updates, [])
        handler(SimpleNamespace(data={'action': 'update', 'old_entity_id': 'climate.source', 'entity_id': 'climate.new'}))
        self.assertEqual(updates[0]['options']['source_entity'], 'climate.new')
        self.assertEqual(updates[0]['unique_id'], 'climate.new')
        self.assertEqual(self.proxy._attr_unique_id, 'entry_climate')


if __name__ == '__main__':
    unittest.main()
