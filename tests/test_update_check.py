import pytest

from src.web.app import _is_newer_version


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("1.3.10", "1.3.9", True),
        ("1.3.9", "1.3.10", False),
        ("1.10.0", "1.9.9", True),
        ("2.0.0", "1.99.99", True),
        ("1.3.4", "1.3.4", False),
        ("1.0.0", "1.3.4", False),
        (None, "1.3.4", False),
        ("1.3.5-beta", "1.3.4", False),
    ],
)
def test_versions_compare_numerically(candidate, current, expected):
    assert _is_newer_version(candidate, current) is expected
