import logging
from enum import IntEnum

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from google.protobuf.message import Message

from ..commands import TimeCommands
from ..devicebase import DeviceBase
from ..packet import Packet
from ..pb import pd335_sys_pb2
from ..props import (
    Field,
    ProtobufProps,
    pb_field,
    proto_attr_mapper,
)

_LOGGER = logging.getLogger(__name__)


def _out_power(x) -> float:
    return -round(x, 2) if x != 0 else 0


def _flow_is_on(x):
    # this is the same check as in app, no idea what values other than 0 (off) or 2 (on)
    # actually represent
    return (int(x) & 0b11) in [0b10, 0b11]


pb = proto_attr_mapper(pd335_sys_pb2.DisplayPropertyUpload)


class DCPortState(IntEnum):
    UNKNOWN = -1
    OFF = 0
    CAR = 1
    SOLAR = 2

    @classmethod
    def from_value(cls, value: int):
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN

    @property
    def state_name(self):
        return self.name.lower()


class Device(DeviceBase, ProtobufProps):
    """Delta 3"""

    SN_PREFIX = (b"P231", b"D361")
    NAME_PREFIX = "EF-D3"

    battery_level = pb_field(pb.cms_batt_soc, lambda value: round(value, 2))
    battery_level_main = pb_field(pb.bms_batt_soc, lambda value: round(value, 2))

    ac_input_power = pb_field(pb.pow_get_ac_in)
    ac_output_power = pb_field(pb.pow_get_ac_out, _out_power)

    input_power = pb_field(pb.pow_in_sum_w)
    output_power = pb_field(pb.pow_out_sum_w)

    dc12v_output_power = pb_field(pb.pow_get_12v, _out_power)
    dc_port_input_power = pb_field(pb.pow_get_pv)
    dc_port_state = pb_field(
        pb.plug_in_info_pv_type, lambda v: DCPortState(v).state_name
    )

    usbc_output_power = pb_field(pb.pow_get_typec1, _out_power)
    usbc2_output_power = pb_field(pb.pow_get_typec2, _out_power)
    usba_output_power = pb_field(pb.pow_get_qcusb1, _out_power)
    usba2_output_power = pb_field(pb.pow_get_qcusb2, _out_power)

    plugged_in_ac = pb_field(pb.plug_in_info_ac_charger_flag)
    energy_backup = pb_field(pb.energy_backup_en)
    energy_backup_battery_level = pb_field(pb.energy_backup_start_soc)
    battery_input_power = pb_field(pb.pow_get_bms, lambda value: max(0, value))
    battery_output_power = pb_field(pb.pow_get_bms, lambda value: -min(0, value))

    battery_charge_limit_min = pb_field(pb.cms_min_dsg_soc)
    battery_charge_limit_max = pb_field(pb.cms_max_chg_soc)

    cell_temperature = pb_field(pb.bms_max_cell_temp)
    dc_12v_port = pb_field(pb.flow_info_12v, _flow_is_on)
    ac_ports = pb_field(pb.flow_info_ac_out, _flow_is_on)
    usb_ports = pb_field(pb.flow_info_qcusb1, _flow_is_on)

    solar_input_power = Field[float]()

    def __init__(
        self, ble_dev: BLEDevice, adv_data: AdvertisementData, sn: str
    ) -> None:
        super().__init__(ble_dev, adv_data, sn)
        self._time_commands = TimeCommands(self)

    @classmethod
    def check(cls, sn):
        return sn[:4] in cls.SN_PREFIX

    @property
    def device(self):
        model = ""
        match self._sn[:4]:
            case "D361":
                model = "1500"
            case "P351":
                model = "Plus"
        return f"Delta 3 {model}".strip()

    async def packet_parse(self, data: bytes) -> Packet:
        return Packet.fromBytes(data, is_xor=True)

    async def data_parse(self, packet: Packet):
        processed = False

        if packet.src == 0x02 and packet.cmdSet == 0xFE and packet.cmdId == 0x15:
            p = pd335_sys_pb2.DisplayPropertyUpload()
            p.ParseFromString(packet.payload)
            # _LOGGER.debug("%s: %s: Parsed data: %r", self.address, self.name, packet)
            _LOGGER.debug("Delta 3 Parsed Message \n %s", str(p))
            self.update_from_message(p)
            processed = True
        elif (
            packet.src == 0x35
            and packet.cmdSet == 0x01
            and packet.cmdId == Packet.NET_BLE_COMMAND_CMD_SET_RET_TIME
        ):
            # Device requested for time and timezone offset, so responding with that
            # otherwise it will not be able to send us predictions and config data
            if len(packet.payload) == 0:
                self._time_commands.async_send_all()
            processed = True

        self.solar_input_power = (
            round(self.dc_port_input_power, 2)
            if (
                self.dc_port_state == DCPortState.SOLAR.state_name
                and self.dc_port_input_power is not None
            )
            else 0
        )
        self._after_message_parsed()

        for field_name in self.updated_fields:
            self.update_callback(field_name)
            self.update_state(field_name, getattr(self, field_name))

        return processed

    def _after_message_parsed(self):
        pass

    async def _send_config_packet(self, message: Message):
        payload = message.SerializeToString()
        packet = Packet(0x20, 0x02, 0xFE, 0x11, payload, 0x01, 0x01, 0x13)
        await self._conn.sendPacket(packet)

    async def set_energy_backup_battery_level(self, value: int):
        config = pd335_sys_pb2.ConfigWrite()
        config.cfg_energy_backup.energy_backup_en = True
        config.cfg_energy_backup.energy_backup_start_soc = value
        await self._send_config_packet(config)
        return True

    async def enable_energy_backup(self, enabled: bool):
        config = pd335_sys_pb2.ConfigWrite()
        config.cfg_energy_backup.energy_backup_en = enabled
        if enabled and self.energy_backup_battery_level is not None:
            config.cfg_energy_backup.energy_backup_start_soc = (
                self.energy_backup_battery_level
            )
        await self._send_config_packet(config)

    async def enable_dc_12v_port(self, enabled: bool):
        await self._send_config_packet(
            pd335_sys_pb2.ConfigWrite(cfg_dc_12v_out_open=enabled)
        )

    async def enable_ac_ports(self, enabled: bool):
        await self._send_config_packet(
            pd335_sys_pb2.ConfigWrite(cfg_ac_out_open=enabled)
        )

    async def set_battery_charge_limit_min(self, limit: int):
        if (
            self.battery_charge_limit_max is not None
            and limit > self.battery_charge_limit_max
        ):
            return False

        await self._send_config_packet(pd335_sys_pb2.ConfigWrite(cfg_min_dsg_soc=limit))
        return True

    async def set_battery_charge_limit_max(self, limit: int):
        if (
            self.battery_charge_limit_min is not None
            and limit < self.battery_charge_limit_min
        ):
            return False

        await self._send_config_packet(pd335_sys_pb2.ConfigWrite(cfg_max_chg_soc=limit))
        return True

    async def enable_usb_ports(self, enabled: bool):
        await self._send_config_packet(pd335_sys_pb2.ConfigWrite(cfg_usb_open=enabled))
