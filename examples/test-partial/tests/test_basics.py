"""
    Pytest Inmanta LSM

    :copyright: 2022 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""


# TODO: test partial fixture
# TODO: test patial behavior
def test_compile(project) -> None:
    project.compile(
        """
        import test_partial
        """
    )
