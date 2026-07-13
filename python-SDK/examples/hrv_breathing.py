"""
HRV-Synced Breathing Example
Synchronize glasses breathing pattern with heart rate

This example shows how to sync the glasses with a heart rate monitor
for coherence training / HRV biofeedback.

Requires:
    pip install edge-glasses bleak
"""

import asyncio
import time
from edge_glasses import Glasses


class HRVBreathing:
    """
    HRV-synchronized breathing trainer
    
    The glasses guide breathing at a rate optimized for HRV coherence,
    typically around 6 breaths per minute (10s per breath cycle).
    """
    
    def __init__(self):
        self.glasses = None
        self.running = False
        
        # Breathing parameters for HRV coherence
        # 6 breaths/min = 10s cycle = 5s inhale + 5s exhale
        self.inhale_time = 5.0
        self.exhale_time = 5.0
    
    async def connect(self):
        """Connect to glasses"""
        print("Connecting to EDGE Glasses...")
        self.glasses = Glasses()
        await self.glasses.connect()
        print("Connected!")
    
    async def disconnect(self):
        """Disconnect and clear"""
        if self.glasses:
            await self.glasses.clear()
            await self.glasses.disconnect()
    
    async def breathing_cycle(self):
        """
        Run one breathing cycle
        Returns elapsed time

        The lens waveform is rendered by the glasses' on-board breathe
        engine (started in run()); this method just keeps our app-side
        breath clock phase-locked to it and prints the pacing prompts.
        """
        cycle_time = self.inhale_time + self.exhale_time
        inhale_pct = round(100 * self.inhale_time / cycle_time)

        # Phase-lock the on-board engine to our breath clock. sync_breath
        # must be sent only at the breath-cycle boundary, never mid-breath
        # (it restarts the breathe waveform at the instant of the write).
        await self.glasses.sync_breath(int(cycle_time * 1000), inhale_pct=inhale_pct)

        # Inhale phase: glasses darken on their own
        print("  Inhale...", end="", flush=True)
        await asyncio.sleep(self.inhale_time)
        print(" done")

        # Exhale phase: glasses clear on their own
        print("  Exhale...", end="", flush=True)
        await asyncio.sleep(self.exhale_time)
        print(" done")

        return cycle_time
    
    async def run(self, duration_minutes: float = 5.0):
        """
        Run HRV breathing session
        
        Args:
            duration_minutes: Session duration
        """
        print(f"\nHRV Coherence Breathing - {duration_minutes} minutes")
        print(f"Breath rate: {60 / (self.inhale_time + self.exhale_time):.1f} breaths/min")
        print("-" * 40)
        
        self.running = True
        start_time = time.time()
        target_time = duration_minutes * 60
        breath_count = 0

        # Start the on-board breathe engine; it renders the lens waveform
        # so we don't stream per-tick opacity over BLE.
        cycle_time = self.inhale_time + self.exhale_time
        bpm = max(1, min(30, round(60 / cycle_time)))
        inhale_pct = round(100 * self.inhale_time / cycle_time)
        await self.glasses.start_breathe(bpm=bpm, inhale_pct=inhale_pct)

        try:
            while self.running and (time.time() - start_time) < target_time:
                breath_count += 1
                elapsed = time.time() - start_time
                remaining = target_time - elapsed
                
                print(f"\nBreath {breath_count} ({remaining:.0f}s remaining)")
                await self.breathing_cycle()
        
        finally:
            self.running = False
            await self.glasses.clear()
        
        print(f"\nSession complete! {breath_count} breaths")
    
    def stop(self):
        """Stop the session"""
        self.running = False


async def main():
    print("EDGE Glasses - HRV Coherence Breathing")
    print("=" * 40)
    print()
    print("This session guides breathing at 6 breaths/minute,")
    print("the optimal rate for heart rate variability coherence.")
    print()
    
    hrv = HRVBreathing()
    
    try:
        await hrv.connect()
        
        duration = input("Duration in minutes (default 5): ").strip()
        duration = float(duration) if duration else 5.0
        
        input("Press Enter to start...")
        await hrv.run(duration_minutes=duration)
        
    except KeyboardInterrupt:
        print("\nStopped!")
    finally:
        await hrv.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
