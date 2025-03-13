import abc
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Callable, cast

from google.protobuf.message import Message

from .protobuf_field import ProtobufField

if TYPE_CHECKING:
    from .protobuf_props import ProtobufProps


class ProtobufRepeatedField[T_ITEM, T_OUT](ProtobufField[T_OUT]):
    def get_list(self, value: Message) -> Sequence[Message]:
        list_attrs = self.pb_field.attrs
        if not list_attrs:
            raise ValueError

        if not value.HasField(list_attrs[0]):
            return []

        for attr in list_attrs:
            value = getattr(value, attr)

        return cast(Sequence[Message], value)

    @abc.abstractmethod
    def process_item(self, value: T_ITEM) -> T_OUT | None: ...

    def __set_name__(self, owner: type["ProtobufProps"], name: str):
        super().__set_name__(owner, name)
        owner.add_repeated_field(self)

    def __set__(self, instance: "ProtobufProps", value: Any):
        if (value := self.process_item(value)) is None:
            return

        self._set_value(instance, value)


def repeated_pb_field_type[T_ITEM, T_OUT](
    list_field: Sequence[T_ITEM],
    value_field: Callable[[T_ITEM], T_OUT] = lambda x: x,
) -> type[ProtobufRepeatedField[T_ITEM, T_OUT]]:
    class CustomRepeatedField(ProtobufRepeatedField[T_ITEM, T_OUT]):
        pb_field = list_field

    return CustomRepeatedField
