#!/usr/bin/env python

from setuptools import setup, find_packages

with open("README.md", "rt") as fh:
    long_description = fh.read()

dependencies = [
    "chia-blockchain==2.1.4",
]

dev_dependencies = [
    "black==23.7.0",
    "pytest",
    "pytest-asyncio",
    "pytest-env",
]

setup(
    name="secure_the_mint",
    version="0.0.1",
    author="Andreas Greimel",
    packages=find_packages(exclude=("tests",)),
    entry_points={
        "console_scripts": [
            "secure_the_mint = secure_the_mint.secure_the_mint:main",
            "unwind_the_mint = secure_the_mint.unwind_the_mint:main"
        ],
    },
    author_email="andreas@mintgarden.io",
    setup_requires=["setuptools_scm"],
    install_requires=dependencies,
    license="https://opensource.org/licenses/Apache-2.0",
    description="Tools to bulk mint many NFTs on-demand using Secure the bag",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "License :: OSI Approved :: Apache Software License",
        "Topic :: Security :: Cryptography",
    ],
    extras_require=dict(
        dev=dev_dependencies,
    ),
)
