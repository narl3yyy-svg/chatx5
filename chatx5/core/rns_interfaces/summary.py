"""RNS interface status summarization."""

def summarize_rns_interfaces(interfaces=None, hub_role="off", hub_port=4242):
    """Collapse duplicate runtime RNS interfaces for status display."""
    if interfaces is None:
        try:
            import RNS

            interfaces = getattr(RNS.Transport, "interfaces", []) or []
        except Exception:
            interfaces = []
    hub_port = int(hub_port or 4242)
    groups = {}
    for iface in interfaces or []:
        cls = type(iface).__name__
        name = str(
            getattr(iface, "name", "") or getattr(iface, "interface_name", "") or ""
        ).strip()
        online = bool(getattr(iface, "online", False))
        if cls == "TCPClientInterface":
            host = (getattr(iface, "target_host", None) or "").strip()
            port = int(
                getattr(iface, "target_port", None)
                or getattr(iface, "port", None)
                or hub_port
            )
            if host:
                key = f"tcp_client_out:{host}:{port}"
                label = f"TCP client → {host}:{port}"
                role = "outbound"
            else:
                key = "tcp_client_inbound"
                label = "Hub relay clients (inbound)"
                role = "inbound"
        elif cls == "TCPServerInterface":
            listen_ip = (getattr(iface, "listen_ip", None) or "0.0.0.0").strip()
            port = int(
                getattr(iface, "listen_port", None)
                or getattr(iface, "port", None)
                or hub_port
            )
            key = f"tcp_server:{listen_ip}:{port}"
            if hub_role == "server":
                label = name or f"TCP hub server :{port}"
            else:
                label = name or f"TCP server {listen_ip}:{port}"
            role = "server"
        elif cls == "UDPInterface":
            key = f"udp:{name or 'lan'}"
            label = name or "UDP LAN"
            role = "lan"
        elif cls == "SerialInterface":
            port = (getattr(iface, "port", None) or name or "serial").strip()
            key = f"serial:{port}"
            label = name or f"Serial {port}"
            role = "serial"
        else:
            key = f"{cls}:{name or cls}"
            label = name or cls
            role = "other"
        bucket = groups.setdefault(
            key,
            {
                "type": cls,
                "name": label,
                "role": role,
                "online": 0,
                "total": 0,
            },
        )
        bucket["total"] += 1
        if online:
            bucket["online"] += 1
    order = {
        "UDPInterface": 0,
        "TCPServerInterface": 1,
        "TCPClientInterface": 2,
        "SerialInterface": 3,
    }
    out = []
    for bucket in groups.values():
        out.append({
            "type": bucket["type"],
            "name": bucket["name"],
            "role": bucket["role"],
            "online": bucket["online"] > 0,
            "count": bucket["total"],
            "online_count": bucket["online"],
        })
    out.sort(key=lambda row: (order.get(row["type"], 9), row["name"]))
    return out
