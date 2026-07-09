"""
OpenBCI Neurofeedback Example
Control glasses based on EEG alpha power

Requires:
    pip install brainflow edge-glasses

Hardware:
    - EDGE Glasses
    - OpenBCI Cyton or compatible board
"""

import asyncio
import numpy as np
from edge_glasses import Glasses

# Uncomment when using real OpenBCI hardware:
# from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
# from brainflow.data_filter import DataFilter, FilterTypes


class MockEEG:
    """Mock EEG for testing without hardware"""
    
    def __init__(self):
        self.t = 0
    
    def get_alpha_power(self) -> float:
        """Simulate alpha power oscillating 0-1"""
        self.t += 0.1
        # Simulate alpha increasing during relaxation
        base = 0.3 + 0.2 * np.sin(self.t * 0.5)
        noise = np.random.normal(0, 0.05)
        return np.clip(base + noise, 0, 1)


class OpenBCIFeedback:
    """
    Real-time neurofeedback using OpenBCI and EDGE Glasses
    
    Higher alpha power = darker glasses (more relaxed)
    Lower alpha power = clearer glasses (needs focus)
    """
    
    def __init__(self, serial_port: str = None, use_mock: bool = True):
        self.use_mock = use_mock
        self.serial_port = serial_port
        self.board = None
        self.glasses = None
        self.running = False
        
        # Alpha band: 8-12 Hz
        self.alpha_low = 8
        self.alpha_high = 12
        
        # Smoothing
        self.alpha_history = []
        self.history_len = 10
        
        # Calibration
        self.alpha_min = 0.0
        self.alpha_max = 1.0
    
    async def connect(self):
        """Connect to both devices"""
        print("Connecting to EDGE Glasses...")
        self.glasses = Glasses()
        await self.glasses.connect()
        print("  Glasses connected!")
        
        if self.use_mock:
            print("Using mock EEG data (no OpenBCI hardware)")
            self.board = MockEEG()
        else:
            print(f"Connecting to OpenBCI on {self.serial_port}...")
            # Uncomment for real hardware:
            # params = BrainFlowInputParams()
            # params.serial_port = self.serial_port
            # self.board = BoardShim(BoardIds.CYTON_BOARD, params)
            # self.board.prepare_session()
            # self.board.start_stream()
            print("  OpenBCI connected!")
    
    async def disconnect(self):
        """Disconnect from devices"""
        if self.glasses:
            await self.glasses.clear()
            await self.glasses.disconnect()
        
        if not self.use_mock and self.board:
            # self.board.stop_stream()
            # self.board.release_session()
            pass
    
    def get_alpha_power(self) -> float:
        """Get current alpha band power"""
        if self.use_mock:
            return self.board.get_alpha_power()
        
        # Real OpenBCI implementation:
        # data = self.board.get_current_board_data(256)  # 1 second at 256 Hz
        # eeg_channels = BoardShim.get_eeg_channels(BoardIds.CYTON_BOARD)
        # 
        # # Get channel 1 (Oz recommended for alpha)
        # channel_data = data[eeg_channels[0]]
        # 
        # # Bandpass filter for alpha (8-12 Hz)
        # DataFilter.perform_bandpass(channel_data, 256, self.alpha_low, 
        #                             self.alpha_high, 4, FilterTypes.BUTTERWORTH, 0)
        # 
        # # Calculate power (RMS)
        # power = np.sqrt(np.mean(channel_data ** 2))
        # return power
        
        return 0.5  # Placeholder
    
    def smooth_alpha(self, alpha: float) -> float:
        """Apply temporal smoothing"""
        self.alpha_history.append(alpha)
        if len(self.alpha_history) > self.history_len:
            self.alpha_history.pop(0)
        return np.mean(self.alpha_history)
    
    def normalize_alpha(self, alpha: float) -> float:
        """Normalize to 0-1 range based on calibration"""
        # Update calibration range
        self.alpha_min = min(self.alpha_min, alpha * 0.9 + self.alpha_min * 0.1)
        self.alpha_max = max(self.alpha_max, alpha * 0.9 + self.alpha_max * 0.1)
        
        # Normalize
        if self.alpha_max > self.alpha_min:
            normalized = (alpha - self.alpha_min) / (self.alpha_max - self.alpha_min)
            return np.clip(normalized, 0, 1)
        return 0.5
    
    async def run(self, duration: float = 60.0):
        """
        Run neurofeedback loop
        
        Args:
            duration: Session duration in seconds
        """
        print(f"\nStarting {duration}s neurofeedback session...")
        print("Higher alpha = darker glasses (relaxed)")
        print("Lower alpha = clearer glasses (alert)")
        print("-" * 40)
        
        self.running = True
        start_time = asyncio.get_event_loop().time()
        
        try:
            while self.running:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= duration:
                    break
                
                # Get and process alpha
                raw_alpha = self.get_alpha_power()
                smoothed = self.smooth_alpha(raw_alpha)
                normalized = self.normalize_alpha(smoothed)
                
                # Map to opacity (0-255)
                opacity = int(normalized * 255)
                
                # Send to glasses
                await self.glasses.set_opacity(opacity)
                
                # Status update every second
                if int(elapsed) != int(elapsed - 0.05):
                    remaining = int(duration - elapsed)
                    bar = "█" * int(normalized * 20) + "░" * (20 - int(normalized * 20))
                    print(f"  [{bar}] Alpha: {normalized:.2f} | Opacity: {opacity:3d} | {remaining}s left")
                
                await asyncio.sleep(0.05)  # 20 Hz — max recommended rate for streaming set_opacity
        
        finally:
            self.running = False
            print("\nSession complete!")
    
    def stop(self):
        """Stop the feedback loop"""
        self.running = False


async def main():
    print("EDGE Glasses - OpenBCI Neurofeedback")
    print("=" * 40)
    print()
    
    # Use mock=True for testing without OpenBCI hardware
    # Set mock=False and provide serial_port for real hardware
    feedback = OpenBCIFeedback(use_mock=True)
    
    try:
        await feedback.connect()
        
        duration = input("Session duration in seconds (default 60): ").strip()
        duration = float(duration) if duration else 60.0
        
        await feedback.run(duration=duration)
        
    except KeyboardInterrupt:
        print("\nInterrupted!")
    finally:
        await feedback.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
