from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="edge-glasses",
    version="2.2.0",
    author="EDGE Technologies",
    author_email="dev@edge-glasses.com",
    description="Python SDK for EDGE Smart Glasses",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/narbiscorp/edge-SDK",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering",
        "Topic :: Scientific/Engineering :: Human Machine Interfaces",
    ],
    python_requires=">=3.8",
    install_requires=[
        "bleak>=0.21.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
        ],
    },
    keywords="ble bluetooth glasses meditation neurofeedback openbci eeg",
    entry_points={
        "console_scripts": [
            "edge-glasses=edge_glasses.cli:cli_main",
        ],
    },
)
