"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""


# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!
def test_basic_example(testdir):
    """Make sure that our plugin works."""

    testdir.copy_example("quickstart")

    result = testdir.runpytest("tests/test_quickstart.py")
    result.assert_outcomes(passed=1)

def test_basic_failed_example(testdir):
    """ Testing that a failed test doesn't make the plugin fail """

    testdir.copy_example("quickstart")

    result = testdir.runpytest("tests/test_quickstart_failed.py")
    result.assert_outcomes(failed=1)
