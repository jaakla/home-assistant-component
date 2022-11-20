"""AirPatrol Smartheat Platform integration."""
import asyncio
import sys, json, time, random, pprint, base64, requests, hmac, hashlib, re
import pickle, urllib
import logging, time, hmac, hashlib, random, base64, json, socket, requests, re, threading, hashlib, string
import voluptuous as vol
import aiohttp

from datetime import timedelta
from datetime import datetime

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import discovery
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    CONF_SCAN_INTERVAL,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_USERNAME,
)

DOMAIN = "airpatrol"
CONF_DEBUG = "debug"
_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=timedelta(seconds=60)
                ): cv.time_period,
                vol.Optional(CONF_DEBUG, default=False): cv.boolean,
            },
            extra=vol.ALLOW_EXTRA,
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config):
    """Setup Airpatrol device."""
    _LOGGER.debug("Create the main object")

    hass.data[DOMAIN] = AirPatrolDevice(hass, config)

    await hass.data[DOMAIN].cached_login(
        hass.data[DOMAIN]._username, hass.data[DOMAIN]._password
    )

    if hass.data[DOMAIN]._session:  # make sure login was successful
        await hass.helpers.discovery.async_load_platform("sensor", DOMAIN, {}, config)

        async def update_devices(event_time):
            asyncio.run_coroutine_threadsafe(
                hass.data[DOMAIN].async_update(), hass.loop
            )

        async_track_time_interval(
            hass, update_devices, await hass.data[DOMAIN].get_scan_interval()
        )

    return True


class AirPatrolDevice:
    def __init__(self, hass, config):

        self._hass = hass
        self._username = config.get(DOMAIN, {}).get(CONF_USERNAME, "")
        self._password = config.get(DOMAIN, {}).get(CONF_PASSWORD, "")
        self._scan_interval = config.get(DOMAIN, {}).get(CONF_SCAN_INTERVAL)

        self._sonoff_debug = config.get(DOMAIN, {}).get(CONF_DEBUG, False)
        self._sonoff_debug_log = []

        self._devices = []
        self._updated = None
        self._params = None  # params resp
        self._diagnostic = None
        self._zones = None
        self._tempsensors = None

        self._session = None  # login resp

        self.SESSION_FILE = "/tmp/session_cache3"
        self.LOGIN_URL = "https://smartheat.airpatrol.eu/"

        # self._params = self.cached_login(self._username, self._password)

    async def get_scan_interval(self):
        if self._scan_interval < timedelta(seconds=60):
            self._scan_interval = timedelta(seconds=60)

        return self._scan_interval

    def get_cid(self):
        return self._session["cid"]

    async def cached_login(self, username, password):
        try:
            _LOGGER.debug("checking " + self.SESSION_FILE)
            with open(self.SESSION_FILE, "rb") as fp:
                _LOGGER.debug("reading " + self.SESSION_FILE)
                self._session = pickle.load(fp)

            # maybe previous session works?
            _LOGGER.debug("update params")
            params = self.update_params()
            _LOGGER.debug("using cached session with params=" + str(params))

        except:
            _LOGGER.debug("exception: " + str(sys.exc_info()[0]))
            params = None

        if params is None:
            # try to login for new session
            _LOGGER.debug("try login")
            # (session_details, headers) = self.do_login(username, password)
            # (session_details, headers) = asyncio.run_coroutine_threadsafe(
            #    self.do_login(username, password), self._hass.loop
            # ).result()
            # await self.do_login(username, password)
            ##--
            _LOGGER.debug("Login with " + username + " " + password)

            # get initial cookie
            headers = {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
            }
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        self.LOGIN_URL, headers=headers, timeout=300
                    ) as r:
                        print("login 1 result: " + str(r.status))
            except:
                _LOGGER.error("Login exception! " + str(sys.exc_info()[0]))
                return

            # real login attempt
            headers["content-type"] = "application/json"
            token = r.cookies["XSRF-TOKEN"].value
            headers["X-XSRF-TOKEN"] = urllib.parse.unquote(token)
            headers["laravel_session"] = urllib.parse.unquote(
                r.cookies["laravel_session"].value
            )
            headers["cookie"] = (
                "XSRF-TOKEN="
                + headers["X-XSRF-TOKEN"]
                + "; laravel_session="
                + headers["laravel_session"]
            )

            app_details = {"password": password, "email": username, "remember": False}
            _LOGGER.debug("login with headers " + str(headers))

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://smartheat.airpatrol.eu/api/login",
                        headers=headers,
                        json=app_details,
                        timeout=300,
                    ) as r:
                        res = await r.json()
                        print("login 2 result: " + str(r.status))
            except:
                _LOGGER.error("Login exception! " + str(sys.exc_info()[0]))

            if r.status != 200:
                _LOGGER.error("login not 200 returned " + str(r.status))
                return None

            # save controller id with headers as high-level cached session var
            headers["cid"] = res["user"]["controllers"][0]["CID"]

            self._session = headers

            # login done, save headers for next time
            _LOGGER.debug(json.dumps(headers, indent=4, sort_keys=True))
            with open(self.SESSION_FILE, "wb") as fp:
                pickle.dump(headers, fp)

        else:
            _LOGGER.debug(json.dumps(params, indent=4, sort_keys=True))
            self._session = params

        return self._session

    async def async_update(self):
        devices = await self.update_devices()

    async def update_devices(self):

        _LOGGER.debug("update_params start with session " + str(self._session))
        url = (
            "https://smartheat.airpatrol.eu/api/controllers/"
            + self._session["cid"]
            + "/params"
        )
        _LOGGER.debug("url " + url)
        req_details = {
            "parameters": [
                "GlobalEcoActive",
                "WifiRSSI",
                "OutdoorTemp",
                "HeatingWaterInletTemp",
                "HeatingWaterRetTemp",
                "HeatingWaterTempDiff",
                "HeatingWaterFlow",
                "CurrentPowerForHeatingHeatingWater",
                "HeatMeterConnectionStatus",
            ]
        }
        _LOGGER.debug("update_params headers " + str(self._session))
        # r = requests.post(url, headers=self._session, json=req_details)
        # self._params = r.json()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=self._session, json=req_details, timeout=5
            ) as r:
                self._params = await r.json()

        _LOGGER.debug("update_params end")
        self._updated = datetime.now()
        return self._params

    def update_all(self):
        _LOGGER.debug("check if have to update")
        if self._updated is None or (
            datetime.now() > (self._updated + self._scan_interval)
        ):
            _LOGGER.info("time to update all")
            if self._session is None:
                _LOGGER.debug("session is None, update yet")
                return

            self._params = self.update_params()
            self._diagnostic = self.update_diagnostic()
            self._zones = self.update_zones()
            self._tempsensors = self.update_sensors()
            self._updated = datetime.now()
            _LOGGER.debug("updated all")
        else:
            _LOGGER.debug("no need to update yet")

    def get_params(self):
        _LOGGER.debug("get_params")
        if self._params is None:
            return self.update_params()

        _LOGGER.debug("cached params returned")
        return self._params

    def get_diagnostic(self):
        if self._diagnostic is None:
            return self.update_diagnostic()
        return self._diagnostic

    def get_zones(self):
        if self._zones is None:
            return self.update_zones()
        return self._zones

    def get_tempsensors(self):
        if self._tempsensors is None:
            return self.update_sensors
        return self._tempsensors

    def update_params(self):
        _LOGGER.debug("update_params start with session " + str(self._session))
        url = (
            "https://smartheat.airpatrol.eu/api/controllers/"
            + self._session["cid"]
            + "/params"
        )
        _LOGGER.debug("url " + url)
        req_details = {
            "parameters": [
                "GlobalEcoActive",
                "WifiRSSI",
                "OutdoorTemp",
                "HeatingWaterInletTemp",
                "HeatingWaterRetTemp",
                "HeatingWaterTempDiff",
                "HeatingWaterFlow",
                "CurrentPowerForHeatingHeatingWater",
                "HeatMeterConnectionStatus",
            ]
        }
        _LOGGER.debug("update_params head " + str(self._session))
        r = requests.post(url, headers=self._session, json=req_details)
        self._params = r.json()
        _LOGGER.debug("update_params end")
        return self._params

    def update_diagnostic(self):
        url = (
            "https://smartheat.airpatrol.eu/api/controllers/"
            + self._session["cid"]
            + "/diagnostic"
        )
        r = requests.get(url, headers=self._session)
        return r.json()

    def update_zones(self):
        url = (
            "https://smartheat.airpatrol.eu/api/controllers/"
            + self._session["cid"]
            + "/zones"
        )
        r = requests.get(url, headers=self._session)
        return r.json()

    def update_sensors(self):
        url = (
            "https://smartheat.airpatrol.eu/api/controllers/"
            + self._session["cid"]
            + "/temperature-sensors"
        )
        r = requests.get(url, headers=self._session)
        return r.json()
