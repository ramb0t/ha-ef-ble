from ..props import pb_field
from . import delta3_common
from .delta3_common import DCPortState, pb


class Device(delta3_common.Device):
    """Delta 3"""

    SN_PREFIX = (b"P231", b"D361")

    dc_input_power = pb_field(pb.pow_get_pv)
    dc_port_state = pb_field(pb.plug_in_info_pv_type, lambda v: DCPortState(v))
