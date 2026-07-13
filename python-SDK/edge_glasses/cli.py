#!/usr/bin/env python3
"""
EDGE Glasses CLI
Command-line control for EDGE Smart Glasses (Narbis_Edge)

Usage:
    edge-glasses scan                  # Find devices
    edge-glasses opacity 128           # Set opacity (0-255)
    edge-glasses clear                 # Clear lenses
    edge-glasses dark                  # Darken lenses
    edge-glasses static 75             # Static hold at 75% duty
    edge-glasses brightness 80         # Max brightness 80%
    edge-glasses strobe 10 50          # 10 Hz strobe, 50% duty
    edge-glasses breathe 6             # 6 BPM breathe mode
    edge-glasses preset relax 10       # 10-min relax preset
    edge-glasses duration 20           # 20-min auto-sleep timer
    edge-glasses sleep                 # Put to sleep
"""

import asyncio
import sys
from edge_glasses import Glasses


async def cmd_scan():
    """Scan for devices"""
    print("Scanning for EDGE Glasses (Narbis_Edge)...")
    devices = await Glasses.scan(timeout=5.0)

    if not devices:
        print("No devices found.")
        print("Note: the glasses stop advertising after 2 minutes idle -")
        print("tap the magnet to wake them, then retry.")
        return

    print(f"Found {len(devices)} device(s):")
    for i, d in enumerate(devices):
        print(f"  {i+1}. {d.name} [{d.address}] RSSI: {d.rssi}")


async def cmd_opacity(value: int):
    """Set opacity"""
    async with Glasses() as g:
        await g.set_opacity(value)
        print(f"Opacity set to {value}")


async def cmd_clear():
    """Clear lenses"""
    async with Glasses() as g:
        await g.clear()
        print("Lenses cleared")


async def cmd_dark():
    """Darken lenses"""
    async with Glasses() as g:
        await g.dark()
        print("Lenses darkened")


async def cmd_static(duty: int):
    """Static mode at duty cycle"""
    async with Glasses() as g:
        await g.set_static(duty)
        print(f"Static mode at {duty}%")


async def cmd_brightness(percent: int):
    """Set max brightness"""
    async with Glasses() as g:
        await g.set_brightness(percent)
        print(f"Brightness set to {percent}%")


async def cmd_strobe(hz, duty_pct):
    """Start strobe mode"""
    async with Glasses() as g:
        await g.start_strobe(hz=hz, duty_pct=duty_pct)
        if hz is not None:
            print(f"Strobing at {hz} Hz" + (f", {duty_pct}% duty" if duty_pct is not None else ""))
        else:
            print("Strobing with stored settings")


async def cmd_breathe(bpm, with_strobe: bool):
    """Start breathe mode"""
    async with Glasses() as g:
        await g.start_breathe(bpm=bpm, with_strobe=with_strobe)
        mode = "breathe+strobe" if with_strobe else "breathe"
        if bpm is not None:
            print(f"Started {mode} at {bpm} BPM")
        else:
            print(f"Started {mode} with stored settings")


async def cmd_preset(preset_type: str, duration: int):
    """Start preset session"""
    async with Glasses() as g:
        if preset_type == "relax":
            await g.session_relax(duration)
        elif preset_type == "focus":
            await g.session_focus(duration)
        elif preset_type == "meditate":
            await g.session_meditate(duration)
        elif preset_type == "sleep":
            await g.session_sleep(duration)
        else:
            print(f"Unknown preset: {preset_type}")
            print("Options: relax, focus, meditate, sleep")
            return
        print(f"Started {preset_type} preset ({duration} min)")


async def cmd_duration(minutes: int):
    """Set session duration"""
    async with Glasses() as g:
        await g.set_duration(minutes)
        print(f"Session duration set to {minutes} min")


async def cmd_sleep():
    """Sleep device"""
    async with Glasses() as g:
        await g.sleep()
        print("Device sleeping")


async def cmd_factory_reset():
    """Factory reset stored settings"""
    async with Glasses() as g:
        await g.factory_reset()
        print("Factory reset sent")


def print_help():
    print(__doc__)
    print("Commands:")
    print("  scan                      Scan for devices")
    print("  opacity <0-255>           Set opacity (legacy 1-byte write)")
    print("  clear                     Clear lenses (opacity 0)")
    print("  dark                      Darken lenses (opacity 255)")
    print("  static <0-100>            Static mode at duty cycle %")
    print("  brightness <0-100>        Set max brightness %")
    print("  strobe [hz] [duty]        Start strobe (1-50 Hz, 10-90% duty)")
    print("  breathe [bpm] [--strobe]  Start breathe mode (1-30 BPM)")
    print("  preset <type> <mins>      Preset session (relax/focus/meditate/sleep)")
    print("  duration <1-60>           Set auto-sleep session length (min)")
    print("  sleep                     Put device to deep sleep")
    print("  factory-reset             Reset stored settings to defaults")


async def main():
    if len(sys.argv) < 2:
        print_help()
        return

    cmd = sys.argv[1].lower()

    try:
        if cmd == "scan":
            await cmd_scan()

        elif cmd == "opacity":
            if len(sys.argv) < 3:
                print("Usage: edge-glasses opacity <0-255>")
                return
            await cmd_opacity(int(sys.argv[2]))

        elif cmd == "clear":
            await cmd_clear()

        elif cmd == "dark":
            await cmd_dark()

        elif cmd == "static":
            if len(sys.argv) < 3:
                print("Usage: edge-glasses static <0-100>")
                return
            await cmd_static(int(sys.argv[2]))

        elif cmd == "brightness":
            if len(sys.argv) < 3:
                print("Usage: edge-glasses brightness <0-100>")
                return
            await cmd_brightness(int(sys.argv[2]))

        elif cmd == "strobe":
            hz = int(sys.argv[2]) if len(sys.argv) > 2 else None
            duty = int(sys.argv[3]) if len(sys.argv) > 3 else None
            await cmd_strobe(hz, duty)

        elif cmd == "breathe":
            args = [a for a in sys.argv[2:] if a != "--strobe"]
            with_strobe = "--strobe" in sys.argv[2:]
            bpm = int(args[0]) if args else None
            await cmd_breathe(bpm, with_strobe)

        elif cmd == "preset":
            if len(sys.argv) < 4:
                print("Usage: edge-glasses preset <type> <minutes>")
                print("Types: relax, focus, meditate, sleep")
                return
            await cmd_preset(sys.argv[2].lower(), int(sys.argv[3]))

        elif cmd == "duration":
            if len(sys.argv) < 3:
                print("Usage: edge-glasses duration <1-60>")
                return
            await cmd_duration(int(sys.argv[2]))

        elif cmd == "sleep":
            await cmd_sleep()

        elif cmd == "factory-reset":
            await cmd_factory_reset()

        elif cmd in ("help", "-h", "--help"):
            print_help()

        else:
            print(f"Unknown command: {cmd}")
            print_help()

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cli_main():
    """Synchronous entry point for CLI"""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
