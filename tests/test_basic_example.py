"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""


# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!
# def test_deployment_failure(testdir):
def a():
    """ Testing that a failed test doesn't make the plugin fail """

    testdir.copy_example("test_service")

    result = testdir.runpytest("tests/test_deployment_failure.py")
    result.assert_outcomes(passed=1)


# def test_basic_example(testdir):
def b():
    """Make sure that our plugin works."""

    testdir.copy_example("quickstart")

    result = testdir.runpytest("tests/test_quickstart.py")
    result.assert_outcomes(passed=1)


def test_client_guard(testdir):
    """ Testing that the ClientGuard class is implemented correctly """

    testdir.copy_example("quickstart")

    result = testdir.runpytest("tests/test_client_guard.py")
    result.assert_outcomes(passed=1)