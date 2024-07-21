#!/usr/bin/env python

import glob
import os
import subprocess
import sys
import textwrap

import setuptools

tests_require = ["coverage", "wheel", "ruff", "mypy", "types-python-dateutil", "types-requests", "types-PyYAML"]

setuptools.setup(
    name="aegea",
    url="https://github.com/kislyuk/aegea",
    license="Apache Software License",
    author="Andrey Kislyuk",
    author_email="kislyuk@gmail.com",
    description="Amazon Web Services Operator Interface",
    long_description=open("README.rst").read(),
    use_scm_version={
        "write_to": "aegea/version.py",
    },
    setup_requires=["setuptools_scm >= 3.4.3"],
    install_requires=[
        "boto3 >= 1.34.46, < 2",
        "argcomplete >= 3.1.4, < 4",
        "paramiko >= 2.12.0, < 4",
        "requests >= 2.31.0, < 3",
        "tweak >= 1.0.4, < 2",
        "pyyaml >= 6.0.1, < 7",
        "python-dateutil >= 2.8.2, < 3",
        "babel >= 2.10.3, < 3",
        "ipwhois >= 1.2.0, < 2",
        "uritemplate >= 4.1.1, < 5",
        "chalice >= 1.31.2, < 2",
    ],
    extras_require={
        "test": tests_require,
    },
    tests_require=tests_require,
    packages=setuptools.find_packages(exclude=["test"]),
    scripts=glob.glob("scripts/*"),
    platforms=["MacOS X", "Posix"],
    test_suite="test",
    include_package_data=True,
)
