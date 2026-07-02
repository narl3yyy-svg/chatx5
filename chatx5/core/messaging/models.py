"""Chat message model (JSON wire format)."""

import json
import time
import uuid

from chatx5.core.messaging.constants import MESSAGE_TYPE_TEXT


class ChatMessage:
    def __init__(
        self,
        msg_type,
        content,
        sender=None,
        timestamp=None,
        file_name=None,
        file_size=None,
        msg_id=None,
    ):
        self.msg_type = msg_type
        self.content = content
        self.sender = sender
        self.timestamp = timestamp or time.time()
        self.file_name = file_name
        self.file_size = file_size
        self.msg_id = msg_id or str(uuid.uuid4())[:12]
        self.hub_group = False

    def to_dict(self):
        d = {
            "type": self.msg_type,
            "content": self.content,
            "timestamp": self.timestamp,
            "msg_id": self.msg_id,
        }
        if self.sender:
            d["sender"] = self.sender
        if self.file_name:
            d["file_name"] = self.file_name
        if self.file_size:
            d["file_size"] = self.file_size
        if self.hub_group:
            d["hub"] = True
        return d

    @classmethod
    def from_dict(cls, d):
        msg = cls(
            msg_type=d.get("type", MESSAGE_TYPE_TEXT),
            content=d.get("content", ""),
            sender=d.get("sender"),
            timestamp=d.get("timestamp", time.time()),
            file_name=d.get("file_name"),
            file_size=d.get("file_size"),
            msg_id=d.get("msg_id"),
        )
        msg.hub_group = bool(d.get("hub"))
        return msg

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data):
        return cls.from_dict(json.loads(data))