"""
Basic EDGE Glasses Example
Demonstrates simple opacity control
"""

import asyncio
from edge_glasses import Glasses


async def main():
    print("Connecting to EDGE Glasses (Narbis_Edge)...")
    # Note: the glasses power their radio down after 2 minutes with no
    # client connected. If the device isn't found, tap the magnet on the
    # temple to re-arm advertising.
    async with Glasses() as glasses:
        print("Connected!")
        
        # Cycle through opacity levels
        print("Cycling opacity...")
        for opacity in range(0, 256, 32):
            print(f"  Opacity: {opacity}")
            await glasses.set_opacity(opacity)
            await asyncio.sleep(0.5)
        
        # Back to clear
        print("Clearing...")
        await glasses.clear()
        await asyncio.sleep(1)
        
        print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
