"""Foundation smoke tests."""

from ai_daily_digest import __version__


def test_package_exposes_version() -> None:
    """The package should be importable in local and CI environments."""
    assert __version__ == "0.1.0"
