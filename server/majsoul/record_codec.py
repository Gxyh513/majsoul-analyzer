"""Decoder for stored Mahjong Soul protobuf records and action streams."""
from __future__ import annotations

import json
from pathlib import Path

from ..paipu_performance import measure
from .proto import LiqiProto
from .errors import MajsoulError


class RecordCodecMixin:
    def _get_record_proto(self, protocol_version: str):
        if self._record_proto is not None and self._record_proto[0] == protocol_version:
            return self._record_proto[1]
        filename = f"liqi-{protocol_version}.json"
        paths = (self.cache_dir / filename, Path(__file__).resolve().parents[2] / "data" / "cache" / filename)
        try:
            path = next(path for path in paths if path.is_file())
            schema = path.read_text(encoding="utf8")
            with measure("JSON解析"):
                schema = json.loads(schema)
            proto = LiqiProto(schema)
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            raise MajsoulError("原始牌谱对应的雀魂协议文件不可用，请恢复协议文件。") from exc
        self._record_proto = (protocol_version, proto)
        return proto

    def decode_record_head(self, record, game_uuid: str) -> dict:
        from google.protobuf.json_format import MessageToDict
        from server.record_store import CorruptRecordError

        proto = self._get_record_proto(record.protocol_version)
        try:
            with measure("Protobuf实际解码"):
                res = proto.decode("ResGameRecord", record.response)
            data = res.data or record.detail
            if res.head.uuid != game_uuid or not data or (res.HasField("error") and res.error.code):
                raise ValueError("invalid game response")
            head = MessageToDict(res.head, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
            return {"head": head}
        except Exception as exc:
            raise CorruptRecordError("原始牌谱无法解码，请从备份恢复；系统不会自动重新下载。") from exc

    def decode_record(self, record, game_uuid: str) -> dict:
        from google.protobuf.json_format import MessageToDict
        from server.record_store import CorruptRecordError

        proto = self._get_record_proto(record.protocol_version)
        try:
            with measure("Protobuf实际解码"):
                res = proto.decode("ResGameRecord", record.response)
            data = res.data or record.detail
            if res.head.uuid != game_uuid or not data or (res.HasField("error") and res.error.code):
                raise ValueError("invalid game response")
            with measure("Protobuf实际解码"):
                name, payload = proto.unwrap(data)
            if name not in (".lq.GameDetailRecords", "lq.GameDetailRecords", "GameDetailRecords"):
                raise ValueError("invalid game detail")
            with measure("Protobuf实际解码"):
                detail = proto.decode(name, payload)
            actions = []
            if getattr(detail, "version", 0) < 210715 and len(detail.records) > 0:
                for rec in detail.records:
                    with measure("Protobuf实际解码"):
                        n, d = proto.unwrap(rec)
                        message = proto.decode(n, d)
                    actions.append(_to_dict(n, message))
            else:
                for act in detail.actions:
                    if act.result:
                        with measure("Protobuf实际解码"):
                            n, d = proto.unwrap(act.result)
                            message = proto.decode(n, d)
                        actions.append(_to_dict(n, message))
            head = MessageToDict(res.head, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
            return {"head": head, "actions": actions}
        except Exception as exc:
            raise CorruptRecordError("原始牌谱无法解码，请从备份恢复；系统不会自动重新下载。") from exc


def _to_dict(name, msg):
    from google.protobuf.json_format import MessageToDict

    d = MessageToDict(msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
    return {"name": name.split(".")[-1], "data": d}


