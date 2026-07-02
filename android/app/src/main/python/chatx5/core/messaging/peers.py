"""Peer hash helpers."""

from chatx5.core.discovery import normalize_hash
from chatx5.core.messaging.constants import HUB_GROUP_PEER


def is_hub_peer_hash(peer_hash):
    clean = normalize_hash(peer_hash)
    return clean in (HUB_GROUP_PEER, "__hub_group__")