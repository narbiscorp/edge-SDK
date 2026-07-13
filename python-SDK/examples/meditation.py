"""
Meditation Session Example
Demonstrates timed session with breathing patterns
"""

import asyncio
from edge_glasses import Glasses


async def main():
    print("EDGE Glasses - Meditation Session")
    print("=" * 40)
    
    async with Glasses() as glasses:
        print("Connected!")
        print()
        
        # Choose session type
        # (v2 presets are fixed-parameter: the firmware no longer ramps
        # settings over the session)
        print("Session types:")
        print("  1. Relax (15 min) - 5 BPM breathing")
        print("  2. Focus (10 min) - breathe + 12 Hz strobe")
        print("  3. Meditate (10 min) - 6 BPM breathing (device default)")
        print("  4. Sleep (20 min) - 4 BPM breathing, auto-sleep after")
        print("  5. Custom session")
        print()
        
        choice = input("Select (1-5): ").strip()
        
        if choice == "1":
            print("Starting relaxation session...")
            await glasses.session_relax(15)

        elif choice == "2":
            print("Starting focus session...")
            await glasses.session_focus(10)

        elif choice == "3":
            print("Starting meditation session...")
            await glasses.session_meditate(10)

        elif choice == "4":
            print("Starting sleep session...")
            await glasses.session_sleep(20)

        elif choice == "5":
            # Custom session
            # (v2: the firmware no longer ramps strobe frequency during a
            # session, so a single fixed Hz replaces the old start/end ramp)
            duration = int(input("Duration (minutes): ") or "10")
            strobe_hz = int(input("Strobe Hz (1-50, 0 for none): ") or "0")
            inhale = float(input("Inhale seconds: ") or "4.0")
            exhale = float(input("Exhale seconds: ") or "4.0")

            # Convert inhale/exhale times to the v2 rate + ratio model
            cycle = inhale + exhale
            bpm = max(1, min(30, round(60 / cycle)))
            inhale_pct = max(10, min(90, round(100 * inhale / cycle)))

            print(f"Starting {duration}-minute custom session...")
            await glasses.set_duration(duration)
            if strobe_hz > 0:
                await glasses.set_strobe_frequency(strobe_hz)
                await glasses.start_breathe(
                    bpm=bpm, inhale_pct=inhale_pct, with_strobe=True
                )
            else:
                await glasses.start_breathe(bpm=bpm, inhale_pct=inhale_pct)
        
        else:
            print("Invalid choice")
            return
        
        print()
        print("Session started! The glasses will auto-sleep when done.")
        print("Close the glasses arms to sleep early.")


if __name__ == "__main__":
    asyncio.run(main())
