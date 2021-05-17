"""
Circadian Lighting Sensor for Home-Assistant.
"""

DEPENDENCIES = ['circadian_lighting']


import logging
import re
import requests
import json
import math
from datetime import timedelta

from custom_components.circadian_lighting import DOMAIN, CIRCADIAN_LIGHTING_UPDATE_TOPIC, DATA_CIRCADIAN_LIGHTING

from homeassistant.helpers.dispatcher import dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.util.color import (
    color_RGB_to_xy, color_temperature_kelvin_to_mired,
    color_temperature_to_rgb, color_xy_to_hs)
import datetime

_LOGGER = logging.getLogger(__name__)

ICON = 'mdi:theme-light-dark'
####################################
hue_gateway = "INPUTHUEIPHERE"
key = "INPUTHUEAPIKEYHERE"
####################################
def update_scene_lights(scene, brightness, x_val, y_val, mired):
	url = "http://" + hue_gateway + "/api/" + key + "/scenes/" + scene + "/"
	r = requests.get(url).json()
	r = r['lights']
	_LOGGER.debug("Updating scene id:" + str(scene))
	for val in r:
		url = "http://" + hue_gateway + "/api/" + key + "/lights/" + str(val)
		t = requests.get(url).json()
		type = t['type']
		url = "http://" + hue_gateway + "/api/" + key + "/scenes/" + scene + "/lightstates/" + str(val)
		if type == 'Color temperature light':
			body = json.dumps({'on': True, 'bri': brightness, 'ct': mired})
		if type == 'Extended color light':
			body = json.dumps({'on': True, 'bri': brightness, 'ct': mired})
		if type == 'Dimmable light':
			body = json.dumps({'on': True, 'bri': brightness})
		r = requests.put(url, data=body)
		_LOGGER.debug("light id: " + str(val) + " body " + str(body) + " status code: " + str(r.status_code))
		if int(r.status_code) != int(200):
			_LOGGER.error("light id: " + str(val) + " body" + str(body) + " status code: " + str(r.status_code))

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Circadian Lighting sensor."""
    cl = hass.data.get(DATA_CIRCADIAN_LIGHTING)
    if cl:
        cs = CircadianSensor(hass, cl)
        add_devices([cs])

        def update(call=None):
            """Update component."""
            cl._update()
        service_name = "values_update"
        hass.services.register(DOMAIN, service_name, update)
        return True
    else:
        return False

class CircadianSensor(Entity):
    """Representation of a Circadian Lighting sensor."""

    def __init__(self, hass, cl):
        """Initialize the Circadian Lighting sensor."""
        self._cl = cl
        self._name = 'Circadian Values'
        self._entity_id = 'sensor.circadian_values'
        self._state = self._cl.data['percent']
        self._unit_of_measurement = '%'
        self._icon = ICON
        self._hs_color = self._cl.data['hs_color']
        self._attributes = {}
        self._attributes['colortemp'] = self._cl.data['colortemp']
        self._attributes['rgb_color'] = self._cl.data['rgb_color']
        self._attributes['xy_color'] = self._cl.data['xy_color']

        """Register callbacks."""
        dispatcher_connect(hass, CIRCADIAN_LIGHTING_UPDATE_TOPIC, self.update_sensor)

    @property
    def entity_id(self):
        """Return the entity ID of the sensor."""
        return self._entity_id

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def hs_color(self):
        return self._hs_color

    @property
    def device_state_attributes(self):
        """Return the attributes of the sensor."""
        return dict((k,str(v) if isinstance(v, datetime.time) or isinstance(v, datetime.timedelta) else v) for k,v in self._attributes.items())

    def update(self):
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        self._cl.update()


    def update_sensor(self):
        if self._cl.data is not None:
            self._state = self._cl.data['percent']
            self._hs_color = self._cl.data['hs_color']
#            self._attributes = self._cl.data
            self._attributes['colortemp'] = self._cl.data['colortemp']
            self._attributes['rgb_color'] = self._cl.data['rgb_color']
            self._attributes['xy_color'] = self._cl.data['xy_color']
	    min_brightness = 30
	    max_brightness = 100
	    brightness = int(((max_brightness - min_brightness) * ((100+self._cl.data['percent']) / 100)) + (min_brightness / 100) * 254)
            ct = color_temperature_kelvin_to_mired(self._cl.data['colortemp'])
            rgb = color_temperature_to_rgb(self._cl.data['colortemp'])
	    _LOGGER.debug("RGB values: " + str(rgb))
            xy = color_RGB_to_xy(rgb[0],rgb[1],rgb[2])

            url = "http://" + hue_gateway + "/api/" + key + "/scenes/"
	    r = requests.get(url).json()

	    scenes = []
            for val in r:
		name = r[val]['name']
		if re.match(r"Circadian", name):
		    scenes.append(val)

	    for val in scenes:
		update_scene_lights(val, brightness, xy[0], xy[1], ct)
