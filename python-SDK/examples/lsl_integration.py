"""
LSL (Lab Streaming Layer) Integration Example
Stream glasses state and receive control from LSL pipelines

LSL is the standard for real-time data streaming in neuroscience research.
This example shows bidirectional integration with LSL-compatible tools.

Requires:
    pip install edge-glasses pylsl

Compatible with:
    - OpenBCI GUI (via LSL)
    - BCI2000
    - BCILAB
    - OpenViBE
    - NeuroPype
    - Any LSL-compatible software
"""

import asyncio
import time
from typing import Optional
from edge_glasses import Glasses

try:
    from pylsl import StreamInfo, StreamOutlet, StreamInlet, resolve_stream
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    print("Warning: pylsl not installed. Run: pip install pylsl")


class GlassesLSLBridge:
    """
    Bidirectional LSL bridge for EDGE Glasses
    
    - Publishes glasses state as LSL outlet (for recording/monitoring)
    - Receives control commands from LSL inlet (for real-time control)
    """
    
    def __init__(self):
        self.glasses: Optional[Glasses] = None
        self.outlet: Optional[StreamOutlet] = None
        self.inlet: Optional[StreamInlet] = None
        self.running = False
        
        # State tracking
        self.current_opacity = 0
        self.last_update = 0
    
    async def connect_glasses(self):
        """Connect to EDGE Glasses"""
        print("Connecting to EDGE Glasses...")
        self.glasses = Glasses()
        await self.glasses.connect()
        print("  Glasses connected!")
    
    def create_outlet(self):
        """Create LSL outlet to publish glasses state"""
        if not LSL_AVAILABLE:
            raise RuntimeError("pylsl not installed")
        
        # Stream info: 1 channel (opacity), 10 Hz, float32
        info = StreamInfo(
            name='EDGE_Glasses_State',
            type='Markers',
            channel_count=1,
            nominal_srate=10,
            channel_format='float32',
            source_id='edge_glasses_001'
        )
        
        # Add metadata
        channels = info.desc().append_child("channels")
        ch = channels.append_child("channel")
        ch.append_child_value("label", "opacity")
        ch.append_child_value("unit", "percent")
        ch.append_child_value("type", "Control")
        
        self.outlet = StreamOutlet(info)
        print("LSL outlet created: EDGE_Glasses_State")
    
    def find_inlet(self, stream_name: str = "GlassesControl", timeout: float = 5.0):
        """
        Find and connect to LSL inlet for receiving commands
        
        Args:
            stream_name: Name of the control stream to find
            timeout: Search timeout in seconds
        """
        if not LSL_AVAILABLE:
            raise RuntimeError("pylsl not installed")
        
        print(f"Looking for LSL stream '{stream_name}'...")
        streams = resolve_stream('name', stream_name, timeout=timeout)
        
        if not streams:
            print(f"  No stream found (will work without external control)")
            return False
        
        self.inlet = StreamInlet(streams[0])
        print(f"  Connected to control stream!")
        return True
    
    def publish_state(self, opacity: float):
        """Publish current state to LSL outlet"""
        if self.outlet:
            self.outlet.push_sample([opacity])
            self.current_opacity = opacity
    
    def check_commands(self) -> Optional[float]:
        """
        Check for incoming commands from LSL inlet
        
        Returns:
            New opacity value (0-1) or None if no command
        """
        if not self.inlet:
            return None
        
        try:
            sample, timestamp = self.inlet.pull_sample(timeout=0.0)
            if sample:
                return float(sample[0])
        except:
            pass
        
        return None
    
    async def run_bridge(self, duration: float = 60.0):
        """
        Run the LSL bridge
        
        This loop:
        1. Checks for incoming control commands
        2. Updates glasses
        3. Publishes state to outlet
        
        Args:
            duration: How long to run (seconds)
        """
        print(f"\nRunning LSL bridge for {duration}s...")
        print("Publishing state to: EDGE_Glasses_State")
        if self.inlet:
            print("Receiving commands from: GlassesControl")
        print("-" * 40)
        
        self.running = True
        start_time = time.time()
        
        try:
            while self.running and (time.time() - start_time) < duration:
                # Check for incoming commands
                command = self.check_commands()
                if command is not None:
                    # Scale 0-1 to 0-255
                    opacity = int(command * 255)
                    await self.glasses.set_opacity(opacity)
                    self.publish_state(command)
                    print(f"  Command received: {command:.2f} -> opacity {opacity}")
                else:
                    # Just publish current state
                    self.publish_state(self.current_opacity)
                
                await asyncio.sleep(0.1)  # 10 Hz — keeps opacity writes under the <= 20 Hz cap
        
        finally:
            self.running = False
    
    async def cleanup(self):
        """Cleanup connections"""
        if self.glasses:
            await self.glasses.clear()
            await self.glasses.disconnect()
        print("Cleanup complete")


class LSLNeurofeedback:
    """
    Receive EEG data from LSL and control glasses
    
    Works with any LSL EEG source:
    - OpenBCI GUI (enable LSL output)
    - Muse via muse-lsl
    - Any LSL-compatible EEG device
    """
    
    def __init__(self):
        self.glasses: Optional[Glasses] = None
        self.inlet: Optional[StreamInlet] = None
        self.running = False
        
        # Processing parameters
        self.alpha_band = (8, 12)  # Hz
        self.baseline_alpha = 1.0
        self.alpha_history = []
        self.history_len = 10
    
    async def connect(self):
        """Connect to glasses and find EEG stream"""
        print("Connecting to EDGE Glasses...")
        self.glasses = Glasses()
        await self.glasses.connect()
        
        if not LSL_AVAILABLE:
            raise RuntimeError("pylsl not installed")
        
        print("Looking for EEG stream...")
        streams = resolve_stream('type', 'EEG', timeout=10.0)
        
        if not streams:
            raise RuntimeError("No EEG stream found!")
        
        self.inlet = StreamInlet(streams[0])
        info = self.inlet.info()
        print(f"  Connected to: {info.name()} ({info.channel_count()} channels, {info.nominal_srate()} Hz)")
    
    def calculate_alpha(self, sample: list) -> float:
        """
        Calculate alpha power from EEG sample
        Simplified - real implementation would use FFT
        """
        # This is a placeholder - real implementation needs proper spectral analysis
        # For actual use, process with scipy.signal or mne
        import numpy as np
        return np.abs(np.mean(sample)) / 100.0  # Normalized placeholder
    
    def smooth(self, value: float) -> float:
        """Apply temporal smoothing"""
        self.alpha_history.append(value)
        if len(self.alpha_history) > self.history_len:
            self.alpha_history.pop(0)
        import numpy as np
        return float(np.mean(self.alpha_history))
    
    async def run(self, duration: float = 60.0):
        """
        Run neurofeedback loop
        
        Higher alpha = darker glasses (relaxed state)
        """
        print(f"\nRunning LSL neurofeedback for {duration}s...")
        print("Higher alpha power = darker glasses")
        print("-" * 40)
        
        self.running = True
        start_time = time.time()
        
        try:
            while self.running and (time.time() - start_time) < duration:
                # Get EEG sample
                sample, timestamp = self.inlet.pull_sample(timeout=0.1)
                
                if sample:
                    # Calculate alpha power
                    alpha = self.calculate_alpha(sample)
                    smoothed = self.smooth(alpha)
                    
                    # Normalize and send to glasses
                    normalized = min(1.0, smoothed / self.baseline_alpha)
                    opacity = int(normalized * 255)
                    
                    await self.glasses.set_opacity(opacity)
                    
                    # Status update
                    elapsed = time.time() - start_time
                    if int(elapsed) % 5 == 0:  # Every 5 seconds
                        print(f"  Alpha: {smoothed:.3f} -> Opacity: {opacity}")
                
                await asyncio.sleep(0.05)  # 20 Hz — max recommended rate for streaming set_opacity
        
        finally:
            self.running = False
            await self.glasses.clear()
    
    async def cleanup(self):
        """Cleanup"""
        if self.glasses:
            await self.glasses.disconnect()


async def demo_bridge():
    """Demo: LSL bridge for external control"""
    bridge = GlassesLSLBridge()
    
    try:
        await bridge.connect_glasses()
        bridge.create_outlet()
        bridge.find_inlet(timeout=2.0)  # Optional control inlet
        
        await bridge.run_bridge(duration=60.0)
    
    except KeyboardInterrupt:
        print("\nStopped!")
    finally:
        await bridge.cleanup()


async def demo_neurofeedback():
    """Demo: EEG neurofeedback via LSL"""
    nf = LSLNeurofeedback()
    
    try:
        await nf.connect()
        await nf.run(duration=60.0)
    
    except KeyboardInterrupt:
        print("\nStopped!")
    finally:
        await nf.cleanup()


async def main():
    print("EDGE Glasses - LSL Integration")
    print("=" * 40)
    print()
    print("1. LSL Bridge (publish state, receive commands)")
    print("2. EEG Neurofeedback (receive EEG, control glasses)")
    print()
    
    choice = input("Select (1-2): ").strip()
    
    if choice == "1":
        await demo_bridge()
    elif choice == "2":
        await demo_neurofeedback()
    else:
        print("Invalid choice")


if __name__ == "__main__":
    asyncio.run(main())
