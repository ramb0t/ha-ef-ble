"""Config flow for EcoFlow BLE integration."""

from __future__ import annotations

import base64
import logging
from functools import cached_property
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    CONN_CLASS_LOCAL_PUSH,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from . import eflib
from .const import CONF_USER_ID, DOMAIN
from .eflib.connection import ConnectionState

_LOGGER = logging.getLogger(__name__)


class EFBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """EcoFlow BLE ConfigFlow"""

    VERSION = 1
    MINOR_VERSION = 0

    CONNECTION_CLASS = CONN_CLASS_LOCAL_PUSH

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_device: eflib.DeviceBase | None = None
        self._discovered_devices: dict[str, eflib.DeviceBase] = {}

        self._user_id: str = ""
        self._email: str = ""
        self._password: str = ""
        self._user_id_validated: bool = False
        self._collapsed = True

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(unique_id=discovery_info.address)
        self._abort_if_unique_id_configured()

        device = eflib.NewDevice(discovery_info.device, discovery_info.advertisement)
        if device is None:
            return self.async_abort(reason="not_supported")
        self._discovery_info = discovery_info
        self._discovered_device = device
        _LOGGER.debug("Discovered device: %s", device)
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        assert self._discovered_device is not None
        device = self._discovered_device
        assert self._discovery_info is not None

        errors = {}
        title = device.name_by_user or device.name
        _LOGGER.debug("Confirm discovery: %s, %s", title, user_input)

        if data := await self._store.async_load():
            self._user_id = data["user_id"]

        self._set_confirm_only()
        placeholders = {"name": title}
        self.context["title_placeholders"] = placeholders

        if user_input is not None:
            errors |= await self._validate_user_id(self._discovered_device, user_input)
            if not errors and self._user_id_validated:
                user_input[CONF_ADDRESS] = device.address
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=placeholders,
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_USER_ID, default=self._user_id): str,
                    vol.Required("login"): section(
                        vol.Schema(
                            {
                                vol.Optional(CONF_EMAIL, default=self._email): str,
                                vol.Optional(
                                    CONF_PASSWORD, default=self._password
                                ): str,
                            }
                        ),
                        {"collapsed": self._collapsed},
                    ),
                    vol.Required(CONF_ADDRESS): vol.In([f"{title} ({device.address})"]),
                }
            ),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        errors = {}

        if data := await self._store.async_load():
            self._user_id = data["user_id"]

        if user_input is not None:
            try:
                device = self._discovered_devices[user_input[CONF_ADDRESS]]
                address = device.address
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                title = device.name_by_user or device.name
                errors |= await self._validate_user_id(device, user_input)
                if not errors and self._user_id_validated:
                    user_input[CONF_ADDRESS] = device.address
                    return self.async_create_entry(title=title, data=user_input)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass, False):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue
            device = eflib.NewDevice(
                discovery_info.device, discovery_info.advertisement
            )
            if device is not None:
                name = device.name_by_user or device.name
                self._discovered_devices[f"{name} ({address})"] = device

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_USER_ID, default=self._user_id): str,
                    vol.Required("login"): section(
                        vol.Schema(
                            {
                                vol.Optional(CONF_EMAIL, default=self._email): str,
                                vol.Optional(
                                    CONF_PASSWORD, default=self._password
                                ): str,
                            }
                        ),
                        {"collapsed": self._collapsed},
                    ),
                    vol.Required(CONF_ADDRESS): vol.In(self._discovered_devices.keys()),
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfiguration of the picked device."""
        reconfigure_entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                address = reconfigure_entry.data.get(CONF_ADDRESS)
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates=user_input,
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USER_ID, default=reconfigure_entry.data.get(CONF_USER_ID)
                    ): str,
                }
            ),
            errors=errors,
        )

    async def _validate_user_id(
        self, device: eflib.DeviceBase, user_input: dict[str, Any]
    ):
        self._user_id_validated = False

        self._email = user_input.get("login", {}).get(CONF_EMAIL, "")
        self._password = user_input.get("login", {}).get(CONF_PASSWORD, "")
        user_id = user_input.get(CONF_USER_ID, "")
        self._collapsed = False

        if not self._email and not self._password and not user_id:
            return {CONF_USER_ID: "User ID cannot be empty"}

        if self._email or self._password:
            if not self._email:
                return {"login": "email_empty"}
            if not self._password:
                return {"login": "password_empty"}
            return await self._ecoflow_login(self._email, self._password)

        self._user_id = user_id

        await device.connect(self._user_id, max_attempts=2)
        await device.waitConnected(timeout=20)
        conn_state = device.connection_state
        await device.disconnect()

        error = None
        match conn_state:
            case ConnectionState.INIT:
                error = "error_try_refresh"
            case ConnectionState.ERROR_AUTH_FAILED:
                error = "device_auth_failed"
            case ConnectionState.ERROR_TIMEOUT:
                error = "bt_timeout"
            case ConnectionState.ERROR_NOT_FOUND:
                error = "bt_timeout"
            case ConnectionState.ERROR_BLEAK:
                error = "bt_general_error"
            case ConnectionState.ERROR_UNKNOWN:
                error = "unknown"
            case ConnectionState.AUTHENTICATED:
                self._user_id_validated = True
                await self._store.async_save(data={"user_id": self._user_id})

        await device.waitDisconnected()

        if error is not None:
            return {"base": error}
        return {}

    @cached_property
    def _store(self):
        return Store(self.hass, self.VERSION, f"{DOMAIN}.user_id")

    async def _ecoflow_login(self, email: str, password: str):
        session = async_get_clientsession(self.hass)
        async with session.post(
            url="https://api.ecoflow.com/auth/login",
            json={
                "scene": "IOT_APP",
                "appVersion": "1.0.0",
                "password": base64.b64encode(password.encode()).decode(),
                "oauth": {
                    "bundleId": "com.ef.EcoFlow",
                },
                "email": email,
                "userType": "ECOFLOW",
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        ) as response:
            response.raise_for_status()

            result_json = await response.json()
            if result_json["code"] != "0":
                return {"login": f"Login failed: '{result_json['message']}'"}

            self._user_id = result_json["data"]["user"]["userId"]
        self._email = ""
        self._password = ""
        self._collapsed = True
        return {}
