import sys

import pytest

from src import platform_support as ps


def test_exactly_one_flag_on_supported_systems() -> None:
    if sys.platform in ("win32", "darwin"):
        assert ps.IS_WINDOWS != ps.IS_MAC


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_supported(platform: str) -> None:
    ps.check_supported(platform)


@pytest.mark.parametrize("platform", ["linux", "freebsd14", "cygwin"])
def test_unsupported(platform: str) -> None:
    with pytest.raises(ps.UnsupportedPlatformError):
        ps.check_supported(platform)
