#!/usr/bin/env python

import os, sys, glob, subprocess, textwrap, setuptools

try:
    # Git version extraction logic designed to be compatible with both semver and PEP 440
    version = subprocess.check_output(["git", "describe", "--tags", "--match", "v*.*.*"]).decode()
    version = version.strip("v\n").replace("-", "+", 1).replace("-", ".")
except Exception:
    version = "0.0.0"

setuptools.setup(
    name="aegea",
    version=version,
    url="https://github.com/kislyuk/aegea",
    license=open("LICENSE.md").readline().strip(),
    author="Andrey Kislyuk",
    author_email="kislyuk@gmail.com",
    description="Amazon Web Services Operator Interface",
    long_description=open("README.rst").read(),
    install_requires=[
        "boto3 >= 1.16.43, < 2",
        "argcomplete >= 1.9.5, < 2",
        "paramiko >= 2.4.2, < 3",
        "requests >= 2.18.4, < 3",
        "tweak >= 1.0.3, < 2",
        "keymaker >= 1.1.0, < 2",
        "pyyaml >= 3.12, < 6",
        "python-dateutil >= 2.6.1, < 3",
        "babel >= 2.4.0, < 3",
        "ipwhois >= 1.1.0, < 2",
        "uritemplate >= 3.0.0, < 4",
        "chalice >= 1.21.7, < 2"
    ],
    extras_require={
        ':python_version == "2.7"': [
            "enum34 >= 1.1.6, < 2",
            "ipaddress >= 1.0.19, < 2",
            "subprocess32 >= 3.2.7, < 4"
        ]
    },
    tests_require=[
        "coverage",
        "flake8",
        "mypy"
    ],
    packages=setuptools.find_packages(exclude=["test"]),
    scripts=glob.glob("scripts/*"),
    platforms=["MacOS X", "Posix"],
    test_suite="test",
    include_package_data=True
)
