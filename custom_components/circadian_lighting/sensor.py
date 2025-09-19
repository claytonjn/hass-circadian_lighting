"""
Circadian Lighting Sensor for Home-Assistant.
"""

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType

from . import CIRCADIAN_LIGHTING_UPDATE_TOPIC, DOMAIN

ICON = "mdi:theme-light-dark"


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info = None,
) -> None:
    """Set up the Circadian Lighting sensor."""
    circadian_lighting = hass.data.get(DOMAIN)
    if circadian_lighting is not None:
        sensor = CircadianSensor(hass, circadian_lighting)
        async_add_entities([sensor])

        async def async_update(call = None) -> None:
            """Update component."""
            await circadian_lighting.async_update()

        service_name = "values_update"
        hass.services.async_register(DOMAIN, service_name, async_update)


class CircadianSensor(Entity):
    """Representation of a Circadian Lighting sensor."""

    def __init__(self, hass, circadian_lighting):
        """Initialize the Circadian Lighting sensor."""
        self._circadian_lighting = circadian_lighting
        self._name = "Circadian Values"
        self._entity_id = "sensor.circadian_values"
        self._unit_of_measurement = "%"
        self._icon = ICON

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
        return self._circadian_lighting._percent

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
        return self._circadian_lighting._hs_color

    @property
    def extra_state_attributes(self):
        """Return the attributes of the sensor."""
        return {
            "colortemp": self._circadian_lighting._colortemp,
            "rgb_color": self._circadian_lighting._rgb_color,
            "xy_color": self._circadian_lighting._xy_color,
        }

    @property
    def should_poll(self) -> bool:
        """Disable polling."""
        return False

    async def async_added_to_hass(self) -> None:
        """Connect dispatcher to signal from CircadianLighting object."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, CIRCADIAN_LIGHTING_UPDATE_TOPIC, self._update_callback
            )
        )

    @callback
    def _update_callback(self) -> None:
        """Triggers update of properties."""
        self.async_schedule_update_ha_state(force_refresh=False)
