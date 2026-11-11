import pytest

from nvr.ipmap import lan_to_vpn, vpn_to_lan


def test_lan_to_vpn_roundtrip():
    assert lan_to_vpn("192.168.1.15", "192.168.1.0/24", "10.20.5.0/24") == "10.20.5.15"
    assert vpn_to_lan("10.20.5.15", "192.168.1.0/24", "10.20.5.0/24") == "192.168.1.15"


def test_lan_outside_raises():
    with pytest.raises(ValueError):
        lan_to_vpn("192.168.2.1", "192.168.1.0/24", "10.20.1.0/24")


def test_requires_slash24():
    with pytest.raises(ValueError):
        lan_to_vpn("10.0.0.5", "10.0.0.0/16", "10.20.1.0/24")
