"""Real Linux IPv6 reject-route fixture for OCR isolation observation."""

import pytest

from scripts.verify_ocr_os_isolation import IsolationError, _ipv6_routes

_NULL_ROUTE = (
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 ffffffff 00000001 00000000 00200200 lo"
)
_LOOPBACK_ROUTE = (
    "00000000000000000000000000000001 80 "
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00000000 00000003 00000000 80200001 lo"
)


def test_kernel_reject_placeholder_does_not_imply_egress():
    assert _ipv6_routes(_NULL_ROUTE + "\n" + _LOOPBACK_ROUTE + "\n") == ["lo"]


@pytest.mark.parametrize(
    "row",
    [
        _NULL_ROUTE.replace("00200200", "00000001"),
        _NULL_ROUTE.replace("ffffffff", "00000001"),
        _NULL_ROUTE.replace(" lo", " eth0"),
        _NULL_ROUTE.replace("00200200", "00200201"),
    ],
)
def test_default_route_variants_remain_refused(row):
    with pytest.raises(IsolationError):
        _ipv6_routes(row)
