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
        "boto3 >= 1.20.35, < 2",
        "argcomplete >= 1.9.5, < 4",
        "paramiko >= 2.4.2, < 4",
        "requests >= 2.18.4, < 3",
        "tweak >= 1.0.4, < 2",
        "pyyaml >= 3.12, < 7",
        "python-dateutil >= 2.6.1, < 3",
        "babel >= 2.4.0, < 3",
        "ipwhois >= 1.1.0, < 2",
        "uritemplate >= 3.0.0, < 4",
        "chalice >= 1.21.7, < 2",
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
