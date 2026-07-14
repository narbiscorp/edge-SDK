"""
Screen Dimmer Replacement -- drive lens tint from ANY protocol's feedback signal

Classic neurofeedback feedback: the training display dims when the trainee
falls out of condition and clears when they are in condition. The Edge
glasses replace that on-screen dimmer with a wearable one -- same contract,
any protocol (SMR, alpha/theta, HEG, EMG down-training, HRV coherence...),
any software that can hand you a feedback value.

Two mapping modes:
    proportional -- tint tracks the signal continuously (analog dimmer)
    threshold    -- classic reward logic: in condition = clear,
                    out of condition = dim to a fixed depth (with smoothing
                    so the transition fades rather than snaps)

Wire it to your protocol by calling LensDimmer.feed(value) with a 0..1
feedback value (1 = in condition / full reward) from wherever your signal
lives -- an LSL inlet, a UDP packet, a serial line, a library callback.

NOTE: the SDK ships the raw plumbing as a built-in --
Glasses.start_feedback_stream() returns a FeedbackStream with the same
decimate/coalesce/serialize writer. This example keeps its own writer to
show the pattern and to add threshold logic + smoothing on top.
Run with --demo for a synthetic signal so you can see it work with no
sensor attached.

Requires:
    pip install edge-glasses
"""

import argparse
import asyncio
import math
import time
from edge_glasses import Glasses

UPDATE_HZ = 12          # production-proven write rate (~20 Hz is the ceiling)
SESSION_MINUTES = 60    # session guard: block auto-sleep for the full hour


class LensDimmer:
    """Map a 0..1 feedback value to lens tint, like a screen dimmer.

    feed() is cheap and thread-safe-ish (single assignment); the writer
    task decimates to UPDATE_HZ, smooths, and coalesces unchanged values
    so the BLE link only carries real changes.
    """

    def __init__(self, glasses: Glasses, mode: str = "threshold",
                 threshold: float = 0.5, dim_depth: int = 80,
                 smoothing: float = 0.3):
        self.glasses = glasses
        self.mode = mode
        self.threshold = threshold      # threshold mode: reward boundary
        self.dim_depth = dim_depth      # threshold mode: tint % when out of condition
        self.smoothing = smoothing      # EWMA factor per tick (1.0 = no smoothing)
        self._value = 0.0               # latest feedback value, 0..1
        self._smoothed_duty = 0.0
        self._last_sent = -1

    def feed(self, value: float) -> None:
        """Feed the latest feedback value (0..1, 1 = in condition)."""
        self._value = max(0.0, min(1.0, value))

    def _target_duty(self) -> float:
        if self.mode == "proportional":
            return (1.0 - self._value) * 100.0
        # threshold: binary reward, like a classic screen dimmer
        return 0.0 if self._value >= self.threshold else float(self.dim_depth)

    async def run(self) -> None:
        """Writer loop: smooth + coalesce + write at UPDATE_HZ."""
        while True:
            target = self._target_duty()
            self._smoothed_duty += self.smoothing * (target - self._smoothed_duty)
            duty = round(self._smoothed_duty)
            if duty != self._last_sent:             # coalesce unchanged values
                await self.glasses.set_static(duty)
                self._last_sent = duty
            await asyncio.sleep(1.0 / UPDATE_HZ)


async def demo_signal(dimmer: LensDimmer) -> None:
    """Synthetic feedback: slow drift in and out of condition (~20 s cycle).

    Replace this with your protocol's real signal -- anything that can
    call dimmer.feed(value) at any rate. Examples in this folder show the
    same pattern fed from OpenBCI, Muse, Polar, and LSL sources.
    """
    t0 = time.monotonic()
    while True:
        t = time.monotonic() - t0
        value = 0.5 + 0.5 * math.sin(2 * math.pi * t / 20.0)
        dimmer.feed(value)
        print(f"\r  feedback={value:.2f}  lens={dimmer._last_sent:>3}%  ", end="")
        await asyncio.sleep(0.05)   # feed() can run at any rate; writes stay at 12 Hz


async def main() -> None:
    ap = argparse.ArgumentParser(description="Wearable screen-dimmer feedback")
    ap.add_argument("--mode", choices=["threshold", "proportional"],
                    default="threshold")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="reward boundary, 0..1 (threshold mode)")
    ap.add_argument("--dim-depth", type=int, default=80,
                    help="tint %% when out of condition (threshold mode)")
    ap.add_argument("--demo", action="store_true",
                    help="run with a synthetic feedback signal")
    args = ap.parse_args()

    async with Glasses() as glasses:
        await glasses.set_duration(SESSION_MINUTES)     # session guard (auto-sleep)
        await glasses.clear()                           # take over from the boot program

        dimmer = LensDimmer(glasses, mode=args.mode,
                            threshold=args.threshold, dim_depth=args.dim_depth)
        print(f"Dimmer running ({args.mode} mode). Ctrl-C to stop.")
        try:
            if args.demo:
                await asyncio.gather(dimmer.run(), demo_signal(dimmer))
            else:
                # Wire your protocol here: start dimmer.run() and call
                # dimmer.feed(value) from your signal source.
                print("No source wired -- pass --demo, or call dimmer.feed() "
                      "from your protocol (see docstring).")
                await dimmer.run()
        finally:
            # The lens FREEZES at its last tint on disconnect -- never leave
            # the wearer dark (see protocol doc, Reconnection).
            await glasses.clear()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped (lens cleared).")
