"""Build Python protobuf message classes at runtime from Mahjong Soul's
``liqi.json`` (protobuf.js JSON descriptor format).

This avoids having to ship / regenerate ``*_pb2.py`` files every time the game
updates its protocol: we simply download the latest ``liqi.json`` and convert it
into a ``FileDescriptorProto``.
"""
from __future__ import annotations

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

_SCALARS = {
    "double": descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE,
    "float": descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT,
    "int64": descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    "uint64": descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
    "int32": descriptor_pb2.FieldDescriptorProto.TYPE_INT32,
    "fixed64": descriptor_pb2.FieldDescriptorProto.TYPE_FIXED64,
    "fixed32": descriptor_pb2.FieldDescriptorProto.TYPE_FIXED32,
    "bool": descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
    "string": descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    "bytes": descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
    "uint32": descriptor_pb2.FieldDescriptorProto.TYPE_UINT32,
    "sfixed32": descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED32,
    "sfixed64": descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED64,
    "sint32": descriptor_pb2.FieldDescriptorProto.TYPE_SINT32,
    "sint64": descriptor_pb2.FieldDescriptorProto.TYPE_SINT64,
}
_LABEL_OPTIONAL = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
_LABEL_REPEATED = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
_TYPE_MESSAGE = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
_TYPE_ENUM = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM


class LiqiProto:
    """Holds message classes and RPC method signatures."""

    def __init__(self, liqi_json: dict):
        self.package = "lq"
        root = liqi_json["nested"]["lq"]["nested"]
        # 1st pass: collect full names of all types so we can resolve refs.
        self._kinds: dict[str, str] = {}  # full name -> "message" | "enum"
        self._collect(root, "." + self.package)

        fdp = descriptor_pb2.FileDescriptorProto()
        fdp.name = "liqi_runtime.proto"
        fdp.package = self.package
        fdp.syntax = "proto3"
        self.methods: dict[str, tuple[str, str]] = {}  # ".lq.Svc.method" -> (req, res)

        for name, node in root.items():
            if "fields" in node:
                self._build_message(fdp.message_type.add(), name, node, "." + self.package)
            elif "values" in node:
                self._build_enum(fdp.enum_type.add(), name, node)
            elif "methods" in node:
                for mname, m in node["methods"].items():
                    self.methods[f".{self.package}.{name}.{mname}"] = (
                        self._resolve(m["requestType"], "." + self.package),
                        self._resolve(m["responseType"], "." + self.package),
                    )

        pool = descriptor_pool.DescriptorPool()
        pool.Add(fdp)
        self._pool = pool
        self._classes: dict[str, type] = {}

    # ------------------------------------------------------------------ build
    def _collect(self, nested: dict, scope: str):
        for name, node in nested.items():
            full = f"{scope}.{name}"
            if "fields" in node:
                self._kinds[full] = "message"
            elif "values" in node:
                self._kinds[full] = "enum"
            if "nested" in node:
                self._collect(node["nested"], full)

    def _resolve(self, type_name: str, scope: str) -> str:
        if type_name.startswith("."):
            return type_name
        # protobuf scoping: search from innermost scope outwards
        parts = scope.split(".")
        while parts:
            cand = ".".join(parts) + "." + type_name
            if cand in self._kinds:
                return cand
            parts.pop()
        cand = f".{self.package}.{type_name}"
        return cand

    def _build_enum(self, edp, name, node):
        edp.name = name
        values = node["values"]
        # proto3 requires first value == 0
        for vname, num in sorted(values.items(), key=lambda kv: (kv[1] != 0, kv[1])):
            v = edp.value.add()
            v.name = f"{name}_{vname}" if False else vname
            v.number = num
        if not any(n == 0 for n in values.values()):
            v = edp.value.add()
            v.name = f"_{name}_UNSPECIFIED"
            v.number = 0
        edp.options.allow_alias = True

    def _build_message(self, dp, name, node, scope):
        dp.name = name
        full = f"{scope}.{name}"
        for sub_name, sub in node.get("nested", {}).items():
            if "fields" in sub:
                self._build_message(dp.nested_type.add(), sub_name, sub, full)
            elif "values" in sub:
                self._build_enum(dp.enum_type.add(), sub_name, sub)
        for fname, f in node["fields"].items():
            fd = dp.field.add()
            fd.name = fname
            fd.number = f["id"]
            ftype = f["type"]
            fd.label = _LABEL_REPEATED if f.get("rule") == "repeated" else _LABEL_OPTIONAL
            if ftype in _SCALARS:
                fd.type = _SCALARS[ftype]
            else:
                resolved = self._resolve(ftype, full)
                fd.type_name = resolved
                fd.type = _TYPE_ENUM if self._kinds.get(resolved) == "enum" else _TYPE_MESSAGE

    # ------------------------------------------------------------------ access
    def get(self, name: str):
        """Get message class by short name (``Wrapper``) or full name (``.lq.Wrapper``)."""
        full = name if name.startswith(".") else f".{self.package}.{name}"
        cls = self._classes.get(full)
        if cls is None:
            desc = self._pool.FindMessageTypeByName(full.lstrip("."))
            cls = message_factory.GetMessageClass(desc)
            self._classes[full] = cls
        return cls

    def decode(self, name: str, data: bytes):
        msg = self.get(name)()
        msg.ParseFromString(data)
        return msg

    def wrap(self, name: str, payload: bytes) -> bytes:
        w = self.get("Wrapper")()
        w.name = name
        w.data = payload
        return w.SerializeToString()

    def unwrap(self, data: bytes):
        w = self.get("Wrapper")()
        w.ParseFromString(data)
        return w.name, w.data
