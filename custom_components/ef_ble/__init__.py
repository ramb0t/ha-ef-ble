"""The unofficial EcoFlow BLE devices integration"""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo

from . import eflib
from .const import CONF_USER_ID, DOMAIN, MANUFACTURER

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
]

type DeviceConfigEntry = ConfigEntry[eflib.DeviceBase]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: DeviceConfigEntry) -> bool:
    """Set up EF BLE device from a config entry."""

    _LOGGER.debug("Init EcoFlow BLE Integration")

    address = entry.data.get(CONF_ADDRESS)
    user_id = entry.data.get(CONF_USER_ID)

    if address is None or user_id is None:
        return False

    if not bluetooth.async_address_present(hass, address):
        raise ConfigEntryNotReady("EcoFlow BLE device not present")

    _LOGGER.debug("Connecting Device")
    discovery_info = bluetooth.async_last_service_info(hass, address, connectable=True)
    device = eflib.NewDevice(discovery_info.device, discovery_info.advertisement)
    if device is None:
        raise ConfigEntryNotReady("EcoFlow BLE Device unable to create")

    await device.connect(user_id)
    entry.runtime_data = device

    _LOGGER.debug("Creating entities")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Setup done")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeviceConfigEntry) -> bool:
    """Unload a config entry."""
    device = entry.runtime_data
    await device.disconnect()
    await device.waitDisconnected()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    return unload_ok


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Device info."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.data.get(CONF_ADDRESS))},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=entry.data.get(CONF_TYPE),
    )
