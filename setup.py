#!/usr/bin/env python
# -*- coding: utf-8 -*-

import codecs
import os

from setuptools import setup


def read(fname):
    file_path = os.path.join(os.path.dirname(__file__), fname)
    return codecs.open(file_path, encoding="utf-8").read()


setup(
    name="pytest-inmanta-lsm",
    version="1.2.0",
    python_requires=">=3.6",  # also update classifiers
    author="Inmanta",
    author_email="code@inmanta.com",
    license="inmanta EULA",
    url="https://github.com/inmanta/pytest-inmanta-lsm",
    description="Common fixtures for inmanta LSM related modules",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    packages=["pytest_inmanta_lsm"],
    package_dir={"": "src"},
    install_requires=["pytest-inmanta", "inmanta-lsm"],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Framework :: Pytest",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Testing",
        "Topic :: System :: Systems Administration",
        "Topic :: Utilities",
    ],
    entry_points={"pytest11": ["inmanta-lsm = pytest_inmanta_lsm.plugin"]},
)
