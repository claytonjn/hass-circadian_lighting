"""
Circadian Lighting Component for Home-Assistant.

This component calculates color temperature and brightness to synchronize
your color changing lights with perceived color temperature of the sky throughout
the day. This gives your environment a more natural feel, with cooler whites during
the midday and warmer tints near twilight and dawn.

In addition, the component sets your lights to a nice warm white at 1% in "Sleep" mode,
which is far brighter than starlight but won't reset your circadian rhythm or break down
too much rhodopsin in your eyes.

Human circadian rhythms are heavily influenced by ambient light levels and
hues. Hormone production, brainwave activity, mood and wakefulness are
just some of the cognitive functions tied to cyclical natural light.
http://en.wikipedia.org/wiki/Zeitgeber

Here's some further reading:

http://www.cambridgeincolour.com/tutorials/sunrise-sunset-calculator.htm
http://en.wikipedia.org/wiki/Color_temperature

Technical notes: I had to make a lot of assumptions when writing this app
    *   There are no considerations for weather or altitude, but does use your
        hub's location to calculate the sun position.
    *   The component doesn't calculate a true "Blue Hour" -- it just sets the
        lights to 2700K (warm white) until your hub goes into Night mode
"""

import bisect
import logging
from datetime import timedelta

import astral
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util
from homeassistant.components.light import ATTR_TRANSITION, VALID_TRANSITION
from homeassistant.const import (
    CONF_ELEVATION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    SUN_EVENT_SUNRISE,
    SUN_EVENT_SUNSET,
)
from homeassistant.helpers.discovery import load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_sunrise,
    async_track_sunset,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util.color import (
    color_RGB_to_xy,
    color_temperature_to_rgb,
    color_xy_to_hs,
)
from homeassistant.helpers.sun import get_astral_location

_LOGGER = logging.getLogger(__name__)

DOMAIN = "circadian_lighting"
CIRCADIAN_LIGHTING_UPDATE_TOPIC = f"{DOMAIN}_update"
SUN_EVENT_NOON = "solar_noon"
SUN_EVENT_MIDNIGHT = "solar_midnight"

CONF_MIN_CT, DEFAULT_MIN_CT = "min_colortemp", 2500
CONF_MAX_CT, DEFAULT_MAX_CT = "max_colortemp", 5500
CONF_INTERVAL, DEFAULT_INTERVAL = "interval", 300
CONF_SUNRISE_OFFSET = "sunrise_offset"
CONF_SUNSET_OFFSET = "sunset_offset"
CONF_SUNRISE_TIME = "sunrise_time"
CONF_SUNSET_TIME = "sunset_time"
DEFAULT_TRANSITION = 60

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_MIN_CT, default=DEFAULT_MIN_CT): vol.All(
                    vol.Coerce(int), vol.Range(min=1000, max=10000)
                ),
                vol.Optional(CONF_MAX_CT, default=DEFAULT_MAX_CT): vol.All(
                    vol.Coerce(int), vol.Range(min=1000, max=10000)
                ),
                vol.Optional(CONF_SUNRISE_OFFSET): cv.time_period_str,
                vol.Optional(CONF_SUNSET_OFFSET): cv.time_period_str,
                vol.Optional(CONF_SUNRISE_TIME): cv.time,
                vol.Optional(CONF_SUNSET_TIME): cv.time,
                vol.Optional(CONF_LATITUDE): cv.latitude,
                vol.Optional(CONF_LONGITUDE): cv.longitude,
                vol.Optional(CONF_ELEVATION): float,
                vol.Optional(CONF_INTERVAL, default=DEFAULT_INTERVAL): cv.time_period,
                vol.Optional(
                    ATTR_TRANSITION, default=DEFAULT_TRANSITION
                ): VALID_TRANSITION,
            }
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


def setup(hass, config):
    """Set up the Circadian Lighting platform."""
    conf = config[DOMAIN]
    hass.data[DOMAIN] = CircadianLighting(
        hass,
        min_colortemp=conf.get(CONF_MIN_CT),
        max_colortemp=conf.get(CONF_MAX_CT),
        sunrise_offset=conf.get(CONF_SUNRISE_OFFSET),
        sunset_offset=conf.get(CONF_SUNSET_OFFSET),
        sunrise_time=conf.get(CONF_SUNRISE_TIME),
        sunset_time=conf.get(CONF_SUNSET_TIME),
        latitude=conf.get(CONF_LATITUDE, hass.config.latitude),
        longitude=conf.get(CONF_LONGITUDE, hass.config.longitude),
        elevation=conf.get(CONF_ELEVATION, hass.config.elevation),
        interval=conf.get(CONF_INTERVAL),
        transition=conf.get(ATTR_TRANSITION),
    )
    load_platform(hass, "sensor", DOMAIN, {}, config)

    return True


class CircadianLighting:
    """Calculate universal Circadian values."""

    def __init__(
        self,
        hass,
        min_colortemp,
        max_colortemp,
        sunrise_offset,
        sunset_offset,
        sunrise_time,
        sunset_time,
        latitude,
        longitude,
        elevation,
        interval,
        transition,
    ):
        self.hass = hass
        self._min_colortemp = min_colortemp
        self._max_colortemp = max_colortemp
        self._sunrise_offset = sunrise_offset
        self._sunset_offset = sunset_offset
        self._manual_sunset = sunset_time
        self._manual_sunrise = sunrise_time
        self._latitude = latitude
        self._longitude = longitude
        self._elevation = elevation
        self._transition = transition

        self._percent = self.calc_percent()
        self._colortemp = self.calc_colortemp()
        self._rgb_color = self.calc_rgb()
        self._xy_color = self.calc_xy()
        self._hs_color = self.calc_hs()

        if self._manual_sunrise is not None:
            async_track_time_change(
                self.hass,
                self.update,
                hour=self._manual_sunrise.hour,
                minute=self._manual_sunrise.minute,
                second=self._manual_sunrise.second,
            )
        else:
            async_track_sunrise(self.hass, self.update, self._sunrise_offset)

        if self._manual_sunset is not None:
            async_track_time_change(
                self.hass,
                self.update,
                hour=self._manual_sunset.hour,
                minute=self._manual_sunset.minute,
                second=self._manual_sunset.second,
            )
        else:
            async_track_sunset(self.hass, self.update, self._sunset_offset)

        async_track_time_interval(self.hass, self.update, interval)

    def _replace_time(self, date, key):
        other_date = self._manual_sunrise if key == "sunrise" else self._manual_sunset
        return date.replace(
            hour=other_date.hour,
            minute=other_date.minute,
            second=other_date.second,
            microsecond=other_date.microsecond,
        )

    def _get_sun_events(self, date):
        if self._manual_sunrise is not None and self._manual_sunset is not None:
            sunrise = self._replace_time(date, "sunrise")
            sunset = self._replace_time(date, "sunset")
            solar_noon = sunrise + (sunset - sunrise) / 2
            solar_midnight = sunset + ((sunrise + timedelta(days=1)) - sunset) / 2
        else:
            try:
                _LOGGER.debug("Astral version: " + astral.__version__)
            except Exception as e:
                _LOGGER.error(e)
            _loc = get_astral_location(self.hass)
            if isinstance(_loc, tuple):
                # Astral v2.2
                location, _ = _loc
            else:
                # Astral v1
                location = _loc
            location.name = "name"
            location.region = "region"
            location.latitude = self._latitude
            location.longitude = self._longitude
            location.elevation = self._elevation

            if self._manual_sunrise is not None:
                sunrise = self._replace_time(date, "sunrise")
            else:
                sunrise = location.sunrise(date)

            if self._manual_sunset is not None:
                sunset = self._replace_time(date, "sunset")
            else:
                sunset = location.sunset(date)

            try:
              solar_noon = location.noon(date)
            except AttributeError:
              solar_noon = location.solar_noon(date)
            try:
              solar_midnight = location.midnight(date)
            except AttributeError:
              solar_midnight = location.solar_midnight(date)

        if self._sunrise_offset is not None:
            sunrise = sunrise + self._sunrise_offset
        if self._sunset_offset is not None:
            sunset = sunset + self._sunset_offset

        datetimes = {
            SUN_EVENT_SUNRISE: sunrise,
            SUN_EVENT_SUNSET: sunset,
            SUN_EVENT_NOON: solar_noon,
            SUN_EVENT_MIDNIGHT: solar_midnight,
        }

        return {
            k: dt.astimezone(dt_util.UTC).timestamp() for k, dt in datetimes.items()
        }

    def _relevant_events(self, now):
        events = []
        for days in [-1, 0, 1]:
            sun_events = self._get_sun_events(now + timedelta(days=days))
            events.extend(list(sun_events.items()))
        events = sorted(events, key=lambda x: x[1])
        index_now = bisect.bisect([ts for _, ts in events], now.timestamp())
        return dict(events[index_now - 2 : index_now + 2])

    def calc_percent(self):
        now = dt_util.utcnow()
        now_ts = now.timestamp()
        today = self._relevant_events(now)
        # Figure out where we are in time so we know which half of the
        # parabola to calculate. We're generating a different
        # sunset-sunrise parabola for before and after solar midnight.
        # because it might not be half way between sunrise and sunset.
        # We're also generating a different parabola for sunrise-sunset.

        # sunrise -> sunset parabola
        if today[SUN_EVENT_SUNRISE] < now_ts < today[SUN_EVENT_SUNSET]:
            h = today[SUN_EVENT_NOON]
            k = 100
            # parabola before solar_noon else after solar_noon
            x = (
                today[SUN_EVENT_SUNRISE]
                if now_ts < today[SUN_EVENT_NOON]
                else today[SUN_EVENT_SUNSET]
            )

        # sunset -> sunrise parabola
        elif today[SUN_EVENT_SUNSET] < now_ts < today[SUN_EVENT_SUNRISE]:
            h = today[SUN_EVENT_MIDNIGHT]
            k = -100
            # parabola before solar_midnight else after solar_midnight
            x = (
                today[SUN_EVENT_SUNSET]
                if now_ts < today[SUN_EVENT_MIDNIGHT]
                else today[SUN_EVENT_SUNRISE]
            )

        y = 0
        a = (y - k) / (h - x) ** 2
        percentage = a * (now_ts - h) ** 2 + k
        return percentage

    def calc_colortemp(self):
        if self._percent > 0:
            delta = self._max_colortemp - self._min_colortemp
            percent = self._percent / 100
            return (delta * percent) + self._min_colortemp
        else:
            return self._min_colortemp

    def calc_rgb(self):
        return color_temperature_to_rgb(self._colortemp)

    def calc_xy(self):
        return color_RGB_to_xy(*self.calc_rgb())

    def calc_hs(self):
        return color_xy_to_hs(*self.calc_xy())

    async def update(self, _=None):
        """Update Circadian Values."""
        self._percent = self.calc_percent()
        self._colortemp = self.calc_colortemp()
        self._rgb_color = self.calc_rgb()
        self._xy_color = self.calc_xy()
        self._hs_color = self.calc_hs()
        async_dispatcher_send(self.hass, CIRCADIAN_LIGHTING_UPDATE_TOPIC)
