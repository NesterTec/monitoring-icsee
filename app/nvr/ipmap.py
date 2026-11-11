"""Map LAN addresses onto a per-site VPN /24 (1:1 host offset)."""

from __future__ import annotations

import ipaddress


def _networks(lan_cidr: str, vpn_cidr: str) -> tuple[ipaddress.IPv4Network, ipaddress.IPv4Network]:
    lan = ipaddress.ip_network(lan_cidr, strict=False)
    vpn = ipaddress.ip_network(vpn_cidr, strict=False)
    if not isinstance(lan, ipaddress.IPv4Network) or not isinstance(vpn, ipaddress.IPv4Network):
        raise ValueError("Only IPv4 CIDRs are supported")
    if lan.prefixlen != 24 or vpn.prefixlen != 24:
        raise ValueError("lan_cidr and vpn_cidr must be /24 networks")
    return lan, vpn


def lan_to_vpn(lan_ip: str, lan_cidr: str, vpn_cidr: str) -> str:
    """Translate a LAN host into the site VPN block (same host offset)."""
    lan, vpn = _networks(lan_cidr, vpn_cidr)
    addr = ipaddress.ip_address(lan_ip.strip())
    if not isinstance(addr, ipaddress.IPv4Address):
        raise ValueError("Only IPv4 addresses are supported")
    if addr not in lan:
        raise ValueError(f"{lan_ip} is outside LAN {lan_cidr}")
    offset = int(addr) - int(lan.network_address)
    return str(ipaddress.IPv4Address(int(vpn.network_address) + offset))


def vpn_to_lan(vpn_ip: str, lan_cidr: str, vpn_cidr: str) -> str:
    """Inverse of lan_to_vpn."""
    lan, vpn = _networks(lan_cidr, vpn_cidr)
    addr = ipaddress.ip_address(vpn_ip.strip())
    if not isinstance(addr, ipaddress.IPv4Address):
        raise ValueError("Only IPv4 addresses are supported")
    if addr not in vpn:
        raise ValueError(f"{vpn_ip} is outside VPN {vpn_cidr}")
    offset = int(addr) - int(vpn.network_address)
    return str(ipaddress.IPv4Address(int(lan.network_address) + offset))
