"""
Evoked Potentials -- use the lens as a visual STIMULATOR (SSVEP / VEP / P300)

Most SDK examples treat the glasses as a feedback *display*. Here they are the
*stimulus*: the electrochromic lens flickers in front of the eyes to evoke a
potential you record on your own EEG amp, time-locked to the stimulus.

Two paradigms, with very different timing requirements:

  SSVEP (steady-state visual evoked potential)
      Strobe the lens at a fixed frequency; the visual cortex produces a
      steady-state response at that frequency and its harmonics. The on-device
      DDS strobe runs on a hardware timer (+/-100 us edges, see the protocol
      doc), so the flicker is clean and BLE latency is IRRELEVANT -- you detect
      the response in the frequency domain, not per flash. This is the strong
      fit for the Edge, and the basis of an SSVEP speller/BCI.

  Transient VEP / P300 (event-related potential)
      Drive discrete flashes from the host and emit a marker at each onset for
      epoch averaging. CAVEAT: onset timing is bounded by BLE write latency +
      the connection interval (~20-30 ms of jitter), so this suits coarse/late
      components (e.g. P300, ~300 ms) -- NOT precise early-latency VEP work.
      For millisecond stimulus timing, gate a photodiode off the lens and use
      that as your trigger instead of the software marker.

The optional LSL marker stream uses type 'Markers' (single channel, string,
irregular rate) -- the conventional shape for event markers, which you record
alongside your EEG and use to cut epochs.

Requires:
    pip install edge-glasses
    pip install pylsl        # optional -- only for the marker stream
"""

import argparse
import asyncio
import time
from edge_glasses import Glasses

try:
    from pylsl import StreamInfo, StreamOutlet
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False

SESSION_MINUTES = 60        # session guard: block auto-sleep during the run
STROBE_DUTY = 50            # 50% dark/clear = symmetric flicker (range 10-90)


def make_marker_outlet():
    """Optional LSL marker outlet (type 'Markers') for epoch cutting."""
    if not LSL_AVAILABLE:
        print("(pylsl not installed -- running without a marker stream)")
        return None
    info = StreamInfo("EdgeStimulus", "Markers", 1, 0, "string", "edge_evoked")
    return StreamOutlet(info)


def mark(outlet, label):
    """Push a stimulus marker (and print it) at the moment of onset."""
    if outlet is not None:
        outlet.push_sample([label])
    print(f"  [{time.strftime('%H:%M:%S')}] {label}")


async def ssvep(glasses: Glasses, outlet, freqs, on_s=6.0, gap_s=2.0):
    """SSVEP: strobe each target frequency for `on_s`, clear between.

    No per-flash timing needed -- the response lives at the strobe frequency
    and its harmonics, so BLE latency does not matter. Integer Hz here; for
    exact non-integer targets (e.g. 13.5 Hz) send the 0.1-Hz form directly:
        await glasses.send_command(0xAB, struct.pack('<H', round(hz * 10)))
    """
    await glasses.set_strobe_duty(STROBE_DUTY)
    for hz in freqs:
        await glasses.set_strobe_frequency(hz)      # 1-50 Hz
        await glasses.start_strobe()                # begin flicker
        mark(outlet, f"ssvep_on_{hz}hz")
        await asyncio.sleep(on_s)
        await glasses.clear()                        # stop flicker, lens clear
        mark(outlet, "ssvep_off")
        await asyncio.sleep(gap_s)


async def transient(glasses: Glasses, outlet, n=40, flash_ms=100,
                    isi_s=1.0, oddball_every=0):
    """Transient VEP / P300: discrete dark flashes, one marker per onset.

    oddball_every > 0 makes every Nth flash a longer "oddball" (a minimal
    P300 paradigm). Timing is host-driven, so expect BLE-scale onset jitter
    -- fine for the P300 window, coarse for early VEP components.
    """
    await glasses.clear()
    for i in range(n):
        is_oddball = oddball_every and (i + 1) % oddball_every == 0
        await glasses.set_opacity(255)               # flash: go dark
        mark(outlet, "oddball" if is_oddball else "standard")
        await asyncio.sleep((flash_ms * (2 if is_oddball else 1)) / 1000.0)
        await glasses.set_opacity(0)                 # back to clear
        await asyncio.sleep(isi_s)


async def main():
    ap = argparse.ArgumentParser(description="Evoked-potential lens stimulator")
    ap.add_argument("--paradigm", choices=["ssvep", "transient"], default="ssvep")
    ap.add_argument("--freqs", type=float, nargs="+", default=[10, 12, 15],
                    help="SSVEP target frequencies in Hz (1-50)")
    ap.add_argument("--trials", type=int, default=40,
                    help="transient: number of flashes")
    ap.add_argument("--oddball-every", type=int, default=0,
                    help="transient: make every Nth flash an oddball (0 = off)")
    args = ap.parse_args()

    outlet = make_marker_outlet()
    async with Glasses() as glasses:
        await glasses.set_duration(SESSION_MINUTES)  # session guard (auto-sleep)
        await glasses.set_brightness(100)            # full-amplitude stimulus
        print(f"Stimulating ({args.paradigm}). Record your EEG time-locked to "
              f"the markers. Ctrl-C to stop.")
        try:
            if args.paradigm == "ssvep":
                await ssvep(glasses, outlet, [int(f) for f in args.freqs])
            else:
                await transient(glasses, outlet, n=args.trials,
                                oddball_every=args.oddball_every)
        finally:
            # The lens FREEZES at its last tint on disconnect -- clear it so
            # the wearer isn't left dark (see protocol doc, Reconnection).
            await glasses.clear()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped (lens cleared).")
