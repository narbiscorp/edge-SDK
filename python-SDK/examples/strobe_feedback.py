"""Carry a live feedback signal on the strobe, correctly.

The companion code for docs/strobe-feedback.md. Read that first if you have not
- the short version is that the obvious approach (stream your feedback value
onto the strobe's dark duty) makes the flash stutter and pulse dark, for
reasons that are invisible from the opcode table.

Three rules, all of them enforced below:

  1. Drive DEPTH (0xA2), never duty (0xAC) and never rate (0xAB). 0xAC calls
     strobe_update() -> strobe_start(), which zeroes the phase accumulator, so
     every write restarts the strobe cycle.
  2. Smooth client-side, with a time constant in SECONDS taken from the
     measured interval between samples. The on-device glide (0xA0) applies to
     commanded static output only and does nothing for a strobe.
  3. Rate-limit the depth writes. 0xA2 is glitch-free but it is persisted to
     NVS on every call.

Run:
    python strobe_feedback.py                 # simulated signal, no hardware needed
    python strobe_feedback.py --live          # find glasses and drive them
    python strobe_feedback.py --live --hz 13.5 --tau 0.8

The simulated mode replays a noisy signal with the statistics of a real
neurofeedback session and prints what the flash depth would do at several time
constants, so you can see the effect before putting glasses on anyone.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import time


# ---------------------------------------------------------------------------


class Smoother:
    """First-order low-pass with a time constant in SECONDS.

    Why not the usual `ema = alpha * v + (1 - alpha) * ema`: that alpha is
    per-SAMPLE, so the same number means different smoothing at a different
    sample rate, and the numbers are far smaller than they look. alpha = 0.6 at
    16 Hz is a 68 ms time constant - not smoothing, a wire. Two shipping
    products had exactly that as their default, both under help text promising
    about a second, and both produced a strobe that pulsed dark.

    tau here is the time to cover ~63% of a step, and it means that whatever
    rate the samples arrive at.
    """

    # A measured interval can also be a dropped packet or a clock step. Letting
    # a ten-second gap through would advance the output most of the way to the
    # new value in a single sample - itself a visible pulse, produced by the
    # filter that is supposed to prevent them.
    MIN_DT = 1.0 / 512.0
    MAX_DT = 0.5

    def __init__(self, tau_s: float = 0.8) -> None:
        self.tau = max(0.0, float(tau_s))
        self._y: float | None = None
        self._t: float | None = None

    def reset(self) -> None:
        """Call on session start, stream restart, and reconnect.

        Without this the next session opens by fading from wherever the last
        one happened to end.
        """
        self._y = None
        self._t = None

    def update(self, value: float, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        dt = (now - self._t) if self._t is not None else 0.0
        self._t = now
        if self._y is None or self.tau <= 0.0:
            self._y = float(value)          # first sample is taken whole
        else:
            dt = min(max(dt, self.MIN_DT), self.MAX_DT)
            self._y += (1.0 - math.exp(-dt / self.tau)) * (value - self._y)
        return self._y


class StrobeFeedback:
    """Maps a 0-100 feedback signal onto the strobe's depth.

    `glasses` is an edge_glasses.Glasses, or anything exposing the same
    set_brightness / start_strobe / set_static coroutines - the simulator below
    uses that to run this exact class with no hardware.
    """

    def __init__(self, glasses, *, rate_hz: float = 10.0, duty_pct: int = 50,
                 depth_min_pct: int = 5, depth_max_pct: int = 50,
                 smoothing_tau_s: float = 0.8, write_deadband: int = 2,
                 write_interval_s: float = 0.2) -> None:
        self.g = glasses
        self.rate_hz = max(0.5, min(50.0, rate_hz))
        self.duty_pct = max(10, min(90, duty_pct))
        # A floor above zero is not decoration. A flash that stops entirely
        # when the client is doing badly reads as hardware that has failed, and
        # it quietly turns proportional feedback into a threshold.
        self.lo = max(0, min(100, depth_min_pct))
        self.hi = max(self.lo, min(100, depth_max_pct))
        self.smoother = Smoother(smoothing_tau_s)
        self.deadband = max(1, write_deadband)
        self.interval = max(0.05, write_interval_s)
        self._sent = -1
        self._last_t = 0.0
        self._running = False
        self.writes = 0                     # so you can count your NVS wear

    async def start(self) -> None:
        # Rate and duty staged BEFORE the mode is entered, so the first flash
        # the wearer sees is the requested one rather than whatever the last
        # client left in NVS.
        await self.g.start_strobe(hz=self.rate_hz, duty_pct=self.duty_pct)
        await self.g.set_brightness(self.lo)
        self.smoother.reset()
        self._sent = self.lo
        self._last_t = time.monotonic()
        self._running = True

    def depth_for(self, value: float, now: float | None = None) -> int:
        """The depth this value maps to. Split out so it can be tested."""
        smoothed = self.smoother.update(max(0.0, min(100.0, value)), now)
        return self.lo + int(round(smoothed * (self.hi - self.lo) / 100.0))

    async def feed(self, value: float) -> None:
        """Call as often as you like, at whatever rate your source produces."""
        if not self._running:
            return
        now = time.monotonic()
        depth = self.depth_for(value, now)
        if (abs(depth - self._sent) >= self.deadband
                and (now - self._last_t) >= self.interval):
            await self.g.set_brightness(depth)
            self._sent, self._last_t = depth, now
            self.writes += 1

    async def stop(self) -> None:
        """A static write is how the firmware is told to stop the running
        program, so this exits strobe mode AND leaves the lens clear."""
        self._running = False
        await self.g.set_static(0)


# ---------------------------------------------------------------------------


class _FakeGlasses:
    """Records what would have gone over the wire."""

    def __init__(self) -> None:
        self.depths: list[int] = []

    async def start_strobe(self, hz=None, duty_pct=None) -> None:
        pass

    async def set_brightness(self, percent: int) -> None:
        self.depths.append(int(percent))

    async def set_static(self, duty: int) -> None:
        pass


def _noisy_signal(n: int, fs: float, seed: int = 1) -> list[float]:
    """A slow true state plus fast noise - the shape a band-ratio actually has.

    Tuned to the statistics of a recorded session: a signal that moves about
    20 points a second, with roughly 7% of its variance in a component slow
    enough for anyone to control volitionally.
    """
    random.seed(seed)
    rho_slow = math.exp(-1.0 / (8.0 * fs))       # 8 s state
    rho_fast = math.exp(-1.0 / (0.3 * fs))       # 0.3 s noise
    slow = fast = 0.0
    out = []
    for _ in range(n):
        slow = rho_slow * slow + random.gauss(0, 5.0 * math.sqrt(1 - rho_slow ** 2))
        fast = rho_fast * fast + random.gauss(0, 18.0 * math.sqrt(1 - rho_fast ** 2))
        out.append(max(0.0, min(100.0, 30.0 + slow + fast)))
    return out


async def simulate() -> None:
    FS, SECS = 16.0, 180
    signal = _noisy_signal(int(FS * SECS), FS)
    print(__doc__.splitlines()[0])
    print("\nSimulated session: %d s at %g Hz, depth travel 5-50%%.\n" % (SECS, FS))
    print("  %-22s %-14s %-14s %s"
          % ("smoothing", "depth move/s", "max jump", "0xA2 writes (NVS)"))
    for label, tau in (("none (tau=0)", 0.0),
                       ("alpha=0.6 @16Hz", 0.068),
                       ("tau=0.2 s", 0.2),
                       ("tau=0.8 s  <- default", 0.8),
                       ("tau=2.0 s", 2.0)):
        fake = _FakeGlasses()
        fb = StrobeFeedback(fake, smoothing_tau_s=tau)
        await fb.start()
        fake.depths.clear()
        # start() stamped _last_t from the real clock; this loop runs on a
        # synthetic one starting at zero, and mixing the two makes every write
        # look like it happened in the future.
        fb._last_t = 0.0
        t = 0.0
        shown = []
        held = fb.lo
        for v in signal:
            t += 1.0 / FS
            depth = fb.depth_for(v, now=t)
            if (abs(depth - fb._sent) >= fb.deadband
                    and (t - fb._last_t) >= fb.interval):
                fb._sent, fb._last_t = depth, t
                fb.writes += 1
                held = depth
            shown.append(held)
        per_sec = [shown[i] for i in range(0, len(shown), int(FS))]
        d = [abs(per_sec[i] - per_sec[i - 1]) for i in range(1, len(per_sec))]
        print("  %-22s %-14s %-14s %s"
              % (label, "%.1f pts" % (sum(d) / len(d)), "%d pts" % max(d),
                 fb.writes))
    print("\n  'alpha=0.6 @16Hz' is the shipped default that produced the bug"
          "\n  report. It is indistinguishable from no smoothing at all.")
    print("\n  Writes matter: 0xA2 is persisted to NVS on every call.")


async def live(args) -> None:
    from edge_glasses import Glasses

    found = await Glasses.scan(timeout=5.0)
    if not found:
        print("no glasses found - open and close the left arm to re-arm BLE")
        return
    print("connecting to %s" % found[0])
    async with Glasses(found[0].address) as g:
        await g.set_disconnect_behavior(fail_clear=True)   # never leave a wearer dark
        fb = StrobeFeedback(g, rate_hz=args.hz, smoothing_tau_s=args.tau)
        await fb.start()
        print("strobing at %.1f Hz, tau %.2f s - Ctrl-C to stop" % (args.hz, args.tau))
        signal = _noisy_signal(int(30.0 * args.seconds), 30.0)
        try:
            for v in signal:
                await fb.feed(v)
                await asyncio.sleep(1 / 30)
        finally:
            # Every exit path, including exceptions. A wearer must never be
            # left strobing because a script raised.
            await fb.stop()
            print("stopped, lens clear (%d depth writes)" % fb.writes)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--live", action="store_true", help="drive real glasses")
    p.add_argument("--hz", type=float, default=10.0, help="strobe rate (Hz)")
    p.add_argument("--tau", type=float, default=0.8, help="smoothing (seconds)")
    p.add_argument("--seconds", type=float, default=60.0, help="run time when --live")
    args = p.parse_args()
    if args.live:
        print("\n*** Photosensitive epilepsy: screen the wearer before running "
              "this. 15-25 Hz is the highest-risk band. ***\n")
    asyncio.run(live(args) if args.live else simulate())


if __name__ == "__main__":
    main()
