"""
Muse EEG Integration Example
Control glasses based on brainwave activity from Muse headband

Requires:
    pip install edge-glasses muselsl mne

Compatible Muse Devices:
    - Muse 2
    - Muse S
    - Muse S (Gen 2)
    - Original Muse (2016)

This example demonstrates:
    1. Stream EEG data from Muse via muselsl/LSL
    2. Calculate band power (alpha, beta, theta)
    3. Control glasses for meditation/focus training
"""

import asyncio
import time
import numpy as np
from typing import Optional, Callable
from collections import deque
from dataclasses import dataclass

from edge_glasses import Glasses

# Try to import muse/LSL libraries
try:
    from muselsl import stream, list_muses
    from pylsl import StreamInlet, resolve_byprop
    MUSE_AVAILABLE = True
except ImportError:
    MUSE_AVAILABLE = False
    print("Warning: muselsl/pylsl not installed.")
    print("Run: pip install muselsl pylsl")


@dataclass
class BandPowers:
    """EEG frequency band powers"""
    delta: float    # 1-4 Hz (deep sleep)
    theta: float    # 4-8 Hz (drowsy, meditation)
    alpha: float    # 8-12 Hz (relaxed, eyes closed)
    beta: float     # 12-30 Hz (active thinking)
    gamma: float    # 30-100 Hz (cognitive processing)


class MuseEEGProcessor:
    """
    Process EEG data from Muse headband
    """
    
    def __init__(self, buffer_seconds: float = 1.0):
        self.inlet: Optional[StreamInlet] = None
        self.sample_rate = 256  # Muse sample rate
        self.buffer_size = int(buffer_seconds * self.sample_rate)
        
        # Circular buffer for each channel (TP9, AF7, AF8, TP10)
        self.buffers = [deque(maxlen=self.buffer_size) for _ in range(4)]
        
        # Band definitions (Hz)
        self.bands = {
            'delta': (1, 4),
            'theta': (4, 8),
            'alpha': (8, 12),
            'beta': (12, 30),
            'gamma': (30, 50)
        }
        
        # Callbacks
        self.on_band_update: Optional[Callable[[BandPowers], None]] = None
    
    def connect_to_stream(self, timeout: float = 10.0) -> bool:
        """Connect to Muse LSL stream"""
        if not MUSE_AVAILABLE:
            raise RuntimeError("muselsl not installed")
        
        print("Looking for Muse EEG stream...")
        streams = resolve_byprop('type', 'EEG', timeout=timeout)
        
        if not streams:
            print("  No EEG stream found!")
            print("  Make sure to run 'muselsl stream' first")
            return False
        
        self.inlet = StreamInlet(streams[0])
        info = self.inlet.info()
        self.sample_rate = info.nominal_srate()
        print(f"  Connected to: {info.name()} ({self.sample_rate} Hz)")
        return True
    
    def process_sample(self, sample: list):
        """Add sample to buffers and calculate band powers"""
        # Add to each channel buffer
        for i, value in enumerate(sample[:4]):  # 4 EEG channels
            self.buffers[i].append(value)
        
        # Calculate band powers if buffer is full
        if len(self.buffers[0]) >= self.buffer_size and self.on_band_update:
            powers = self.calculate_band_powers()
            self.on_band_update(powers)
    
    def calculate_band_powers(self) -> BandPowers:
        """Calculate power in each frequency band"""
        from scipy import signal
        
        # Average across channels
        data = np.array([list(b) for b in self.buffers])
        avg_data = np.mean(data, axis=0)
        
        # Calculate PSD
        freqs, psd = signal.welch(avg_data, fs=self.sample_rate, nperseg=min(256, len(avg_data)))
        
        # Extract band powers
        def band_power(low, high):
            idx = np.logical_and(freqs >= low, freqs <= high)
            return np.mean(psd[idx]) if np.any(idx) else 0
        
        return BandPowers(
            delta=band_power(1, 4),
            theta=band_power(4, 8),
            alpha=band_power(8, 12),
            beta=band_power(12, 30),
            gamma=band_power(30, 50)
        )


class MuseMeditationTrainer:
    """
    Meditation training with Muse and EDGE Glasses
    
    Uses alpha/theta ratio as meditation depth indicator.
    Higher alpha = deeper relaxation = darker glasses.
    """
    
    def __init__(self):
        self.glasses: Optional[Glasses] = None
        self.processor: Optional[MuseEEGProcessor] = None
        self.running = False
        
        # Meditation metrics
        self.alpha_baseline = 1.0
        self.current_alpha = 0.0
        self.meditation_score = 0.0
        self.score_history = []
        
        # Smoothing
        self.alpha_history = deque(maxlen=10)
    
    async def connect(self):
        """Connect to Muse and glasses"""
        print("=== Muse Meditation Training ===")
        print()
        
        # Connect glasses
        print("Connecting to EDGE Glasses...")
        self.glasses = Glasses()
        await self.glasses.connect()
        print("  Glasses ready!")
        
        # Connect to Muse stream
        self.processor = MuseEEGProcessor()
        self.processor.on_band_update = self._on_band_update
        
        if not self.processor.connect_to_stream():
            raise RuntimeError("Failed to connect to Muse stream")
    
    def _on_band_update(self, powers: BandPowers):
        """Process band powers and update glasses"""
        # Track alpha
        self.alpha_history.append(powers.alpha)
        self.current_alpha = np.mean(self.alpha_history)
        
        # Calculate meditation score (alpha/beta ratio)
        # Higher alpha relative to beta = more relaxed
        if powers.beta > 0:
            ratio = powers.alpha / powers.beta
            self.meditation_score = min(1.0, ratio / 2.0)  # Normalize
        
        self.score_history.append(self.meditation_score)
    
    async def calibrate(self, duration: float = 30.0):
        """
        Calibrate baseline (eyes closed, relaxed)
        """
        print(f"\nCalibrating for {duration}s...")
        print("Close your eyes and relax.")
        
        self.running = True
        start = time.time()
        alpha_samples = []
        
        while self.running and (time.time() - start) < duration:
            # Pull samples
            sample, _ = self.processor.inlet.pull_sample(timeout=0.1)
            if sample:
                self.processor.process_sample(sample)
                alpha_samples.append(self.current_alpha)
            
            await asyncio.sleep(0.01)
        
        if alpha_samples:
            self.alpha_baseline = np.mean(alpha_samples)
            print(f"  Baseline alpha: {self.alpha_baseline:.4f}")
    
    async def run(self, duration_minutes: float = 10.0):
        """
        Run meditation session
        
        Glasses darken as you enter deeper meditation (higher alpha).
        """
        print(f"\nStarting {duration_minutes}-minute meditation...")
        print("Higher alpha = darker glasses = deeper meditation")
        print("-" * 40)
        
        self.running = True
        start = time.time()
        duration_seconds = duration_minutes * 60
        last_write = 0.0

        try:
            while self.running and (time.time() - start) < duration_seconds:
                # Pull EEG samples
                sample, _ = self.processor.inlet.pull_sample(timeout=0.1)
                if sample:
                    self.processor.process_sample(sample)

                # Update glasses based on meditation score
                # (EEG is pulled fast, but lens writes are capped at <= 20 Hz)
                if time.time() - last_write >= 0.05:
                    opacity = int(self.meditation_score * 255)
                    await self.glasses.set_opacity(opacity)
                    last_write = time.time()

                # Status update every 10 seconds
                elapsed = time.time() - start
                if int(elapsed) % 10 == 0 and int(elapsed) != int(elapsed - 0.1):
                    remaining = duration_seconds - elapsed
                    avg_score = np.mean(self.score_history[-100:]) if self.score_history else 0
                    print(f"  Score: {avg_score:.2f} | Alpha: {self.current_alpha:.4f} | {int(remaining)}s left")
                
                await asyncio.sleep(0.01)
        
        finally:
            self.running = False
            await self.glasses.clear()
        
        # Session summary
        print()
        print("=== Session Complete ===")
        if self.score_history:
            print(f"Average meditation score: {np.mean(self.score_history):.2f}")
            print(f"Peak meditation score: {np.max(self.score_history):.2f}")
    
    async def cleanup(self):
        """Cleanup connections"""
        if self.glasses:
            await self.glasses.clear()
            await self.glasses.disconnect()


class MuseFocusTrainer:
    """
    Focus training with Muse and EDGE Glasses
    
    Uses beta/theta ratio as focus indicator.
    Higher beta (focus) = clearer glasses.
    Lower beta (distracted) = darker glasses.
    """
    
    def __init__(self):
        self.glasses: Optional[Glasses] = None
        self.processor: Optional[MuseEEGProcessor] = None
        self.running = False
        
        self.focus_score = 0.0
        self.score_history = []
    
    async def connect(self):
        """Connect to devices"""
        print("=== Muse Focus Training ===")
        print()
        
        self.glasses = Glasses()
        await self.glasses.connect()
        
        self.processor = MuseEEGProcessor()
        self.processor.on_band_update = self._on_band_update
        self.processor.connect_to_stream()
    
    def _on_band_update(self, powers: BandPowers):
        """Calculate focus score from band powers"""
        # Focus = high beta, low theta
        if powers.theta > 0:
            ratio = powers.beta / powers.theta
            self.focus_score = min(1.0, ratio / 3.0)
        
        self.score_history.append(self.focus_score)
    
    async def run(self, duration_minutes: float = 10.0):
        """
        Run focus session
        
        Glasses are clearer when focused, darker when distracted.
        This provides immediate feedback to maintain focus.
        """
        print(f"\nStarting {duration_minutes}-minute focus session...")
        print("Stay focused = clear glasses")
        print("Mind wandering = darker glasses")
        print("-" * 40)
        
        self.running = True
        start = time.time()
        duration_seconds = duration_minutes * 60
        last_write = 0.0

        try:
            while self.running and (time.time() - start) < duration_seconds:
                sample, _ = self.processor.inlet.pull_sample(timeout=0.1)
                if sample:
                    self.processor.process_sample(sample)

                # Higher focus = clearer glasses (inverted from meditation)
                # (EEG is pulled fast, but lens writes are capped at <= 20 Hz)
                if time.time() - last_write >= 0.05:
                    opacity = int((1 - self.focus_score) * 255)
                    await self.glasses.set_opacity(opacity)
                    last_write = time.time()

                await asyncio.sleep(0.01)
        
        finally:
            self.running = False
            await self.glasses.clear()
        
        if self.score_history:
            print(f"\nAverage focus score: {np.mean(self.score_history):.2f}")
    
    async def cleanup(self):
        if self.glasses:
            await self.glasses.disconnect()


def start_muse_stream():
    """Helper to start Muse LSL stream"""
    if not MUSE_AVAILABLE:
        print("muselsl not installed. Run: pip install muselsl")
        return
    
    print("Searching for Muse devices...")
    muses = list_muses()
    
    if not muses:
        print("No Muse devices found!")
        print("Make sure your Muse is:")
        print("  1. Turned on")
        print("  2. Not connected to another app")
        print("  3. In Bluetooth range")
        return
    
    print(f"Found: {muses[0]['name']}")
    print("Starting LSL stream...")
    stream(muses[0]['address'])


async def main():
    print("EDGE Glasses - Muse EEG Integration")
    print("=" * 40)
    print()
    
    if not MUSE_AVAILABLE:
        print("Required: pip install muselsl pylsl mne scipy")
        return
    
    print("Make sure 'muselsl stream' is running first!")
    print()
    print("1. Meditation Training (alpha feedback)")
    print("2. Focus Training (beta feedback)")
    print("3. Start Muse Stream (helper)")
    print()
    
    choice = input("Select (1-3): ").strip()
    
    if choice == "1":
        trainer = MuseMeditationTrainer()
        try:
            await trainer.connect()
            
            calibrate = input("Calibrate baseline? (y/n): ").strip().lower()
            if calibrate == 'y':
                await trainer.calibrate(30)
            
            duration = input("Session duration in minutes (default 10): ").strip()
            duration = float(duration) if duration else 10.0
            await trainer.run(duration)
        except KeyboardInterrupt:
            print("\nStopped!")
        finally:
            await trainer.cleanup()
    
    elif choice == "2":
        trainer = MuseFocusTrainer()
        try:
            await trainer.connect()
            await trainer.run(10.0)
        except KeyboardInterrupt:
            print("\nStopped!")
        finally:
            await trainer.cleanup()
    
    elif choice == "3":
        start_muse_stream()
    
    else:
        print("Invalid choice")


if __name__ == "__main__":
    asyncio.run(main())
