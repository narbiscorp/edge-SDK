"""
EDGE Glasses Python SDK
Control smart LCD glasses over Bluetooth Low Energy
"""

from .glasses import Glasses, ScanResult, Waveform
from .exceptions import (
    GlassesError,
    ConnectionError,
    DeviceNotFoundError,
    CommandError,
    TimeoutError
)

__version__ = "2.0.0"
__all__ = [
    "Glasses",
    "ScanResult",
    "Waveform",
    "GlassesError",
    "ConnectionError",
    "DeviceNotFoundError",
    "CommandError",
    "TimeoutError"
]
