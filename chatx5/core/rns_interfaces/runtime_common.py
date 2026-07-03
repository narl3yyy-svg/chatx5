"""Shared helpers for hot-adding RNS interfaces."""

def _finalize_rns_interface(iface, ifac_size=16):
    """Apply the same post-init fields Reticulum sets when loading config."""
    import RNS
    from RNS.Interfaces.Interface import Interface

    iface.mode = Interface.MODE_FULL
    iface.OUT = True
    iface.IN = True
    iface.ifac_size = ifac_size
    iface.announce_cap = RNS.Reticulum.ANNOUNCE_CAP / 100.0
    iface.announce_rate_target = None
    iface.announce_rate_grace = None
    iface.announce_rate_penalty = None
    if not hasattr(iface, "ifac_netname"):
        iface.ifac_netname = None
    if not hasattr(iface, "ifac_netkey"):
        iface.ifac_netkey = None
    if hasattr(iface, "optimise_mtu"):
        iface.optimise_mtu()
    if hasattr(iface, "final_init"):
        iface.final_init()


def _register_hot_rns_interface(iface, ifac_size=16):
    """Register a runtime-hot-added interface the same way Reticulum config load does."""
    import RNS

    _finalize_rns_interface(iface, ifac_size=ifac_size)
    inst = RNS.Reticulum.get_instance()
    if inst is not None and hasattr(inst, "_add_interface"):
        inst._add_interface(
            iface,
            ifac_size=ifac_size,
            ifac_netname=getattr(iface, "ifac_netname", None),
            ifac_netkey=getattr(iface, "ifac_netkey", None),
        )
    else:
        RNS.Transport.add_interface(iface)
    return iface
