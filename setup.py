#!/usr/bin/env python
from setuptools import setup

setup(
    name="tap-freshcaller",
    version="0.1.4",
    description="Singer.io tap for extracting data",
    author="Stitch",
    url="http://singer.io",
    classifiers=["Programming Language :: Python :: 3 :: Only"],
    py_modules=["tap_freshcaller"],
    install_requires=[
        # NB: Pin these to a more specific version for tap reliability
        "singer-python==5.12.2",
        "requests",
    ],
    entry_points="""
    [console_scripts]
    tap-freshcaller=tap_freshcaller:main
    """,
    packages=["tap_freshcaller"],
    package_data = {
        "schemas": ["tap_freshcaller/schemas/*.json"]
    },
    include_package_data=True,
)
