import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from google.protobuf.message import Message

from ..commands import TimeCommands
from ..devicebase import DeviceBase
from ..packet import Packet
from ..pb import pr705_pb2
from ..props import (
    Field,
    ProtobufProps,
    pb_field,
    proto_attr_mapper,
    repeated_pb_field_type,
)

_LOGGER = logging.getLogger(__name__)

pb = proto_attr_mapper(pr705_pb2.DisplayPropertyUpload)


@dataclass
class _StatField(
    repeated_pb_field_type(
        list_field=pb.display_statistics_sum.list_info,
        value_field=lambda x: x.statistics_content,
    )
):
    stat: pr705_pb2.STATISTICS_OBJECT

    def process_item(self, value: pr705_pb2.StatisticsRecordItem) -> int | None:
        return (
            value.statistics_content if value.statistics_object == self.stat else None
        )


def _out_power(x) -> float:
    return -round(x, 2) if x != 0 else 0


def _flow_is_on(x) -> bool:
    # this is the same check as in app, no idea what values other than 0 (off) or 2 (on)
    # actually represent
    return (int(x) & 0b11) in [0b10, 0b11]


class Device(DeviceBase, ProtobufProps):
    """River 2"""

    SN_PREFIX = (b"R611")
    NAME_PREFIX = "EF-R2"

    battery_level = pb_field(pb.cms_batt_soc)

    ac_input_power = pb_field(pb.pow_get_ac_in)
    ac_input_energy = _StatField(pr705_pb2.STATISTICS_OBJECT_AC_IN_ENERGY)

    ac_output_power = pb_field(pb.pow_get_ac_out, _out_power)
    ac_output_energy = _StatField(pr705_pb2.STATISTICS_OBJECT_AC_OUT_ENERGY)

    input_power = pb_field(pb.pow_in_sum_w)
    input_energy = Field[int]()

    output_power = pb_field(pb.pow_out_sum_w)
    output_energy = Field[int]()

    dc_input_power = pb_field(pb.pow_get_pv)
    dc_input_energy = _StatField(pr705_pb2.STATISTICS_OBJECT_PV_IN_ENERGY)

    dc12v_output_power = pb_field(pb.pow_get_12v)
    dc12v_output_energy = _StatField(pr705_pb2.STATISTICS_OBJECT_DC12V_OUT_ENERGY)

    usbc_output_power = pb_field(pb.pow_get_typec1, _out_power)
    usbc_output_energy = _StatField(pr705_pb2.STATISTICS_OBJECT_TYPEC_OUT_ENERGY)

    usba_output_power = pb_field(pb.pow_get_qcusb1, _out_power)
    usba_output_energy = _StatField(pr705_pb2.STATISTICS_OBJECT_USBA_OUT_ENERGY)

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
            case "R611":
                model = "(512Wh)"
        return f"River 2 {model}".strip()

    async def packet_parse(self, data: bytes) -> Packet:
        return Packet.fromBytes(data, is_xor=True)

    async def data_parse(self, packet: Packet):
        processed = False

        if packet.src == 0x02 and packet.cmdSet == 0xFE and packet.cmdId == 0x15:
            p = pr705_pb2.DisplayPropertyUpload()
            p.ParseFromString(packet.payload)
            _LOGGER.debug("%s: %s: Parsed data: %r", self.address, self.name, packet)
            # _LOGGER.debug("River 2 Parsed Message \n %s", str(p))
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

        if self.ac_input_energy is not None and self.dc_input_energy is not None:
            self.input_energy = self.ac_input_energy + self.dc_input_energy

        if (
            self.ac_output_energy is not None
            and self.usba_output_energy is not None
            and self.usbc_output_energy is not None
            and self.dc12v_output_energy is not None
        ):
            self.output_energy = (
                self.ac_output_energy
                + self.usba_output_energy
                + self.usbc_output_energy
                + self.dc12v_output_energy
            )

        for field_name in self.updated_fields:
            self.update_callback(field_name)
            self.update_state(field_name, getattr(self, field_name))

        return processed

    async def _send_config_packet(self, message):
        payload = message.SerializeToString()
        packet = Packet(0x20, 0x02, 0xFE, 0x11, payload, 0x01, 0x01, 0x13)
        await self._conn.sendPacket(packet)

    async def set_energy_backup_battery_level(self, value: int):
        config = pr705_pb2.ConfigWrite()
        config.cfg_energy_backup.energy_backup_en = True
        config.cfg_energy_backup.energy_backup_start_soc = value
        await self._send_config_packet(config)
        return True

    async def enable_energy_backup(self, enabled: bool):
        config = pr705_pb2.ConfigWrite()
        config.cfg_energy_backup.energy_backup_en = enabled
        if enabled and self.energy_backup_battery_level is not None:
            config.cfg_energy_backup.energy_backup_start_soc = (
                self.energy_backup_battery_level
            )
        await self._send_config_packet(config)

    async def enable_dc_12v_port(self, enabled: bool):
        config = pr705_pb2.ConfigWrite()
        config.cfg_dc_12v_out_open = enabled
        await self._send_config_packet(config)

    async def enable_ac_ports(self, enabled: bool):
        config = pr705_pb2.ConfigWrite()
        config.cfg_ac_out_open = enabled
        await self._send_config_packet(config)

    async def set_battery_charge_limit_min(self, limit: int):
        if (
            self.battery_charge_limit_max is not None
            and limit > self.battery_charge_limit_max
        ):
            return False

        config = pr705_pb2.ConfigWrite()
        config.cfg_min_dsg_soc = limit

        await self._send_config_packet(config)
        return True

    async def set_battery_charge_limit_max(self, limit: int):
        if (
            self.battery_charge_limit_min is not None
            and limit < self.battery_charge_limit_min
        ):
            return False

        config = pr705_pb2.ConfigWrite()
        config.cfg_max_chg_soc = limit

        await self._send_config_packet(config)
        return True
