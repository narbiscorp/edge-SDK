"""
EDGE Glasses Python SDK
Control smart LCD glasses over Bluetooth Low Energy
"""

from .glasses import Glasses, ScanResult, Waveform, FeedbackStream
from .exceptions import (
    GlassesError,
    ConnectionError,
    DeviceNotFoundError,
    CommandError,
    TimeoutError
)

__version__ = "2.1.0"
__all__ = [
    "Glasses",
    "FeedbackStream",
    "ScanResult",
    "Waveform",
    "GlassesError",
    "ConnectionError",
    "DeviceNotFoundError",
    "CommandError",
    "TimeoutError"
]
