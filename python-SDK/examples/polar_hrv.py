"""
Polar Heart Rate Integration Example
Control glasses based on heart rate variability (HRV) from Polar sensors

Requires:
    pip install edge-glasses bleak

Compatible Polar Devices:
    - Polar H10 (recommended - best HRV accuracy)
    - Polar H9
    - Polar Verity Sense
    - Polar OH1

This example demonstrates:
    1. Connect to Polar HR sensor via BLE
    2. Calculate real-time HRV metrics
    3. Control glasses for HRV coherence training
"""

import asyncio
import time
import struct
from typing import Optional, List, Callable
from collections import deque
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from edge_glasses import Glasses


# Polar BLE UUIDs
HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"


@dataclass
class HRVMetrics:
    """Heart rate variability metrics"""
    hr: float           # Heart rate (BPM)
    rmssd: float        # Root mean square of successive differences (ms)
    sdnn: float         # Standard deviation of NN intervals (ms)
    coherence: float    # HRV coherence score (0-1)


class PolarHRMonitor:
    """
    Polar Heart Rate Monitor with HRV calculation
    """
    
    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.device_address: Optional[str] = None
        
        # RR interval history (last 60 seconds)
        self.rr_intervals: deque = deque(maxlen=120)
        self.last_hr = 0
        
        # Callbacks
        self.on_hr_update: Optional[Callable[[int, List[int]], None]] = None
        self.on_hrv_update: Optional[Callable[[HRVMetrics], None]] = None
    
    async def scan(self, timeout: float = 10.0) -> List[str]:
        """Scan for Polar devices"""
        print("Scanning for Polar devices...")
        devices = []
        
        discovered = await BleakScanner.discover(timeout=timeout)
        for d in discovered:
            if d.name and ("Polar" in d.name or "H10" in d.name or "H9" in d.name):
                print(f"  Found: {d.name} [{d.address}]")
                devices.append(d.address)
        
        return devices
    
    async def connect(self, address: Optional[str] = None):
        """Connect to Polar device"""
        if not address:
            devices = await self.scan()
            if not devices:
                raise RuntimeError("No Polar devices found")
            address = devices[0]
        
        print(f"Connecting to {address}...")
        self.client = BleakClient(address)
        await self.client.connect()
        
        # Start HR notifications
        await self.client.start_notify(HR_MEASUREMENT_UUID, self._hr_callback)
        print("  Connected and receiving HR data!")
    
    async def disconnect(self):
        """Disconnect from device"""
        if self.client and self.client.is_connected:
            await self.client.stop_notify(HR_MEASUREMENT_UUID)
            await self.client.disconnect()
    
    def _hr_callback(self, sender, data: bytearray):
        """Parse HR measurement notification"""
        # First byte is flags
        flags = data[0]
        hr_format_16bit = flags & 0x01
        rr_present = (flags >> 4) & 0x01
        
        # Parse heart rate
        if hr_format_16bit:
            hr = struct.unpack('<H', data[1:3])[0]
            offset = 3
        else:
            hr = data[1]
            offset = 2
        
        self.last_hr = hr
        
        # Parse RR intervals if present
        rr_list = []
        if rr_present:
            while offset + 1 < len(data):
                rr = struct.unpack('<H', data[offset:offset+2])[0]
                rr_ms = rr * 1000 / 1024  # Convert to ms
                rr_list.append(rr_ms)
                self.rr_intervals.append(rr_ms)
                offset += 2
        
        # Callbacks
        if self.on_hr_update:
            self.on_hr_update(hr, rr_list)
        
        # Calculate HRV if we have enough data
        if len(self.rr_intervals) >= 10 and self.on_hrv_update:
            metrics = self.calculate_hrv()
            self.on_hrv_update(metrics)
    
    def calculate_hrv(self) -> HRVMetrics:
        """Calculate HRV metrics from RR intervals"""
        import numpy as np
        
        rr = np.array(list(self.rr_intervals))
        
        # Heart rate from average RR
        hr = 60000 / np.mean(rr) if np.mean(rr) > 0 else 0
        
        # RMSSD - parasympathetic activity indicator
        diffs = np.diff(rr)
        rmssd = np.sqrt(np.mean(diffs ** 2)) if len(diffs) > 0 else 0
        
        # SDNN - overall HRV
        sdnn = np.std(rr)
        
        # Coherence score (simplified)
        # High coherence = rhythmic HRV pattern around 0.1 Hz
        # This is a simplified version - real coherence needs spectral analysis
        coherence = min(1.0, rmssd / 100.0)  # Normalized
        
        return HRVMetrics(hr=hr, rmssd=rmssd, sdnn=sdnn, coherence=coherence)


class HRVCoherenceTrainer:
    """
    HRV Coherence Training with EDGE Glasses

    Uses glasses to guide breathing for optimal HRV coherence.
    Coherence is computed app-side from the Polar RR intervals (the
    firmware's on-board coherence pipeline is legacy and unused) and
    fed back as lens brightness.
    """
    
    def __init__(self):
        self.glasses: Optional[Glasses] = None
        self.polar: Optional[PolarHRMonitor] = None
        self.running = False
        
        # Breathing parameters for coherence (6 breaths/min)
        self.breath_rate = 6  # breaths per minute
        self.inhale_ratio = 0.4  # 40% inhale, 60% exhale
        
        # Feedback
        self.current_coherence = 0.0
        self.coherence_history = []
    
    async def connect(self):
        """Connect to both devices"""
        print("=== HRV Coherence Training ===")
        print()
        
        # Connect glasses
        print("Connecting to EDGE Glasses...")
        self.glasses = Glasses()
        await self.glasses.connect()
        print("  Glasses ready!")
        
        # Connect Polar
        self.polar = PolarHRMonitor()
        self.polar.on_hrv_update = self._on_hrv
        await self.polar.connect()
    
    def _on_hrv(self, metrics: HRVMetrics):
        """Handle HRV update"""
        self.current_coherence = metrics.coherence
        self.coherence_history.append(metrics.coherence)
    
    async def run(self, duration_minutes: float = 5.0):
        """
        Run coherence training session
        
        The glasses guide breathing while providing coherence feedback.
        Higher coherence = more visible breathing pattern.
        """
        print(f"\nStarting {duration_minutes}-minute coherence session...")
        print(f"Breathing rate: {self.breath_rate} breaths/min")
        print()
        print("Breathe with the glasses:")
        print("  Darkening = Inhale")
        print("  Clearing = Exhale")
        print()
        print("Better coherence = stronger visual effect")
        print("-" * 40)
        
        self.running = True
        start_time = time.time()
        duration_seconds = duration_minutes * 60

        # Breathing timing
        breath_period = 60.0 / self.breath_rate
        cycle_ms = int(breath_period * 1000)
        inhale_pct = round(self.inhale_ratio * 100)

        # The glasses' on-board breathe engine renders the guide waveform;
        # we don't stream per-tick opacity over BLE to draw it.
        await self.glasses.start_breathe(bpm=self.breath_rate, inhale_pct=inhale_pct)

        try:
            while self.running and (time.time() - start_time) < duration_seconds:
                # Phase-lock the on-board engine once per breath. sync_breath
                # must only be sent at the breath-cycle boundary, never
                # mid-breath (it restarts the waveform instantly).
                await self.glasses.sync_breath(cycle_ms, inhale_pct=inhale_pct)

                # Wait out one full breath cycle (inhale + exhale)
                await asyncio.sleep(breath_period)

                # Coherence feedback (computed app-side from RR intervals):
                # higher coherence = brighter, more visible breathing effect.
                # Min 30% effect even with low coherence.
                coherence_scale = 0.3 + 0.7 * self.current_coherence
                await self.glasses.set_brightness(int(coherence_scale * 100))

                # Status update
                elapsed = time.time() - start_time
                remaining = duration_seconds - elapsed
                avg_coherence = sum(self.coherence_history[-30:]) / max(1, len(self.coherence_history[-30:]))
                print(f"  Coherence: {avg_coherence:.2f} | {int(remaining)}s remaining")

        finally:
            self.running = False
            await self.glasses.clear()
        
        # Session summary
        print()
        print("=== Session Complete ===")
        if self.coherence_history:
            import numpy as np
            print(f"Average coherence: {np.mean(self.coherence_history):.2f}")
            print(f"Peak coherence: {np.max(self.coherence_history):.2f}")
    
    async def cleanup(self):
        """Cleanup connections"""
        if self.polar:
            await self.polar.disconnect()
        if self.glasses:
            await self.glasses.clear()
            await self.glasses.disconnect()


class SimpleHRFeedback:
    """
    Simple heart rate feedback
    
    Higher HR = darker glasses (for relaxation training)
    Lower HR = clearer glasses
    """
    
    def __init__(self):
        self.glasses: Optional[Glasses] = None
        self.polar: Optional[PolarHRMonitor] = None
        self.running = False
        
        # HR range for mapping
        self.hr_min = 60   # Below this = fully clear
        self.hr_max = 100  # Above this = fully dark
        self.hr_target = 65  # Target resting HR
    
    async def connect(self):
        """Connect to devices"""
        self.glasses = Glasses()
        await self.glasses.connect()
        
        self.polar = PolarHRMonitor()
        self.polar.on_hr_update = self._on_hr
        await self.polar.connect()
    
    async def _on_hr(self, hr: int, rr_list: List[int]):
        """Update glasses based on HR"""
        if not self.glasses:
            return

        # Polar notifies at ~1 Hz, well under the <= 20 Hz opacity-write cap
        # Map HR to opacity
        # Higher HR = darker (to encourage relaxation)
        normalized = (hr - self.hr_min) / (self.hr_max - self.hr_min)
        normalized = max(0, min(1, normalized))
        
        opacity = int(normalized * 255)
        await self.glasses.set_opacity(opacity)
    
    async def run(self, duration: float = 60.0):
        """Run HR feedback loop"""
        print(f"Running HR feedback for {duration}s...")
        print(f"HR range: {self.hr_min}-{self.hr_max} BPM")
        print("Higher HR = darker glasses")
        
        self.running = True
        start = time.time()
        
        while self.running and (time.time() - start) < duration:
            print(f"  HR: {self.polar.last_hr} BPM")
            await asyncio.sleep(5)
        
        self.running = False
    
    async def cleanup(self):
        """Cleanup"""
        if self.polar:
            await self.polar.disconnect()
        if self.glasses:
            await self.glasses.clear()
            await self.glasses.disconnect()


async def main():
    print("EDGE Glasses - Polar HR Integration")
    print("=" * 40)
    print()
    print("1. HRV Coherence Training (guided breathing)")
    print("2. Simple HR Feedback (HR -> opacity)")
    print()
    
    choice = input("Select (1-2): ").strip()
    
    if choice == "1":
        trainer = HRVCoherenceTrainer()
        try:
            await trainer.connect()
            duration = input("Duration in minutes (default 5): ").strip()
            duration = float(duration) if duration else 5.0
            await trainer.run(duration)
        except KeyboardInterrupt:
            print("\nStopped!")
        finally:
            await trainer.cleanup()
    
    elif choice == "2":
        feedback = SimpleHRFeedback()
        try:
            await feedback.connect()
            await feedback.run(60.0)
        except KeyboardInterrupt:
            print("\nStopped!")
        finally:
            await feedback.cleanup()
    
    else:
        print("Invalid choice")


if __name__ == "__main__":
    asyncio.run(main())
