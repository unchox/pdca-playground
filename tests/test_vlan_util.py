"""Acceptance oracle for the vlan-util e2e2 task (module does not exist yet)."""

import pytest


def test_parse_lowercase_prefix():
    import vlan_util

    assert vlan_util.parse_vlan("vlan101") == 101


def test_parse_uppercase_prefix():
    import vlan_util

    assert vlan_util.parse_vlan("VLAN200") == 200


def test_parse_plain_number():
    import vlan_util

    assert vlan_util.parse_vlan("300") == 300


def test_invalid_text_raises():
    import vlan_util

    with pytest.raises(ValueError):
        vlan_util.parse_vlan("ethernet")


def test_out_of_range_raises():
    import vlan_util

    with pytest.raises(ValueError):
        vlan_util.parse_vlan("vlan5000")  # valid VLAN IDs are 1..4094
