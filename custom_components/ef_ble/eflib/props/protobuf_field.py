from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, overload

from google.protobuf.message import Message

from .updatable_props import Field

if TYPE_CHECKING:
    from .protobuf_props import ProtobufProps


class _ProtoAttr:
    def __init__(self, name: str):
        self.attrs = [name]

    def __getattr__(self, name):
        self.attrs.append(name)
        return self

    def __str__(self):
        return f"proto_attr({self.attrs})"

    @property
    def name(self):
        return ".".join(self.attrs)


@dataclass
class _ProtoAttrAccessor[T1: Message]:
    message_type: type[T1]

    def __getattr__(self, name: str):
        if name not in self.message_type.DESCRIPTOR.fields_by_name:
            raise AttributeError(
                f"{self.message_type} does not contain field named '{name}'"
            )
        return _ProtoAttr(name)


def proto_attr_mapper[T: Message](pb: type[T]) -> type[T]:
    return _ProtoAttrAccessor(pb)  # type: ignore reportReturnType


class ProtobufField[T](Field[T]):
    def __init__(
        self, pb_field: _ProtoAttr, transform_value: Callable[[Any], T] = lambda x: x
    ):
        self.pb_field = pb_field
        self.transform_value = transform_value

    def _get_value(self, value: Message):
        for attr in self.pb_field.attrs:
            if not value.HasField(attr):
                return None
            value = getattr(value, attr)
        return value

    def __set__(self, instance: "ProtobufProps", value: Any):
        if not isinstance(value, Message):
            raise TypeError(
                f"Can only set value from protobuf message but received: '{value}', "
                f"field {self.pb_field}"
            )

        if (value := self._get_value(value)) is None:
            return

        super().__set__(instance, self.transform_value(value))


@overload
def pb_field[ATTR](attr: ATTR, transform: None = None) -> "ProtobufField[ATTR]": ...


@overload
def pb_field[ATTR, T_OUT](
    attr: ATTR, transform: Callable[[ATTR], T_OUT]
) -> "ProtobufField[T_OUT]": ...


def pb_field(
    attr: Any, transform: Callable[[Any], Any] | None = None
) -> "ProtobufField[Any]":
    """Protocol buffer"""
    return ProtobufField(
        pb_field=attr,
        transform_value=transform if transform is not None else lambda x: x,
    )
