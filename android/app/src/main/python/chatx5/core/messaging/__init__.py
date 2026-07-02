"""Messaging package — link management, connect, queue, and transfer (split from monolith)."""

from chatx5.core.lan_rns import (
    interface_family,
    serial_interface_online,
    suppress_offline_lan_transports,
)
from chatx5.core.messaging.backend import MessagingBackend  # noqa: E402

# Re-export constants used by tests and callers (stable public API).
from chatx5.core.messaging.constants import (  # noqa: E402, F401
    APP_NAME,
    HUB_GROUP_PEER,
    LAN_HTTP_CHUNK,
    LAN_HTTP_MIN_BYTES,
    MESSAGE_TYPE_EMOJI,
    MESSAGE_TYPE_FILE,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_LAN_HTTP,
    MESSAGE_TYPE_LONGTEXT,
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_TRANSFER_CANCEL,
    MESSAGE_TYPE_VIDEO,
    MESSAGE_TYPE_VOICE,
    SERIAL_ANNOUNCE_BURST_COUNT,
    SERIAL_ANNOUNCE_BURST_INTERVAL_S,
    SERIAL_CONNECT_PRIME_INTERVAL_S,
)
from chatx5.core.messaging.models import ChatMessage
from chatx5.core.messaging.peers import is_hub_peer_hash
from chatx5.core.rns_interfaces import (
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    dedupe_serial_interfaces,
    prune_dead_serial_interfaces,
)
from chatx5.core.serial_transfer import is_serial_interface
from chatx5.utils.platform import physical_lan_reachable

__all__ = [
    "APP_NAME",
    "ChatMessage",
    "HUB_GROUP_PEER",
    "LAN_HTTP_CHUNK",
    "LAN_HTTP_MIN_BYTES",
    "MESSAGE_TYPE_EMOJI",
    "MESSAGE_TYPE_FILE",
    "MESSAGE_TYPE_IMAGE",
    "MESSAGE_TYPE_LAN_HTTP",
    "MESSAGE_TYPE_LONGTEXT",
    "MESSAGE_TYPE_TEXT",
    "MESSAGE_TYPE_TRANSFER_CANCEL",
    "MESSAGE_TYPE_VIDEO",
    "MESSAGE_TYPE_VOICE",
    "MessagingBackend",
    "SERIAL_ANNOUNCE_BURST_COUNT",
    "SERIAL_ANNOUNCE_BURST_INTERVAL_S",
    "SERIAL_CONNECT_PRIME_INTERVAL_S",
    "is_hub_peer_hash",
]