# Carrying a feedback signal on the strobe

**Read this before you modulate a running strobe from a live signal.** Doing the
obvious thing produces a flash that stutters and pulses dark between updates, and
it is not obvious why. Two shipping products hit it independently and fixed it the
same way; this is that fix, written down.

If you only want a *fixed* strobe — set a rate, start it, leave it — you do not
need this document. [§4.6.6 of the protocol reference](bluetooth-protocol.md#466-strobe)
covers that in six lines and is complete.

---

## 1. The one rule

**Drive exactly one strobe parameter from your signal, and make it `0xA2`.**

A strobe has three parameters. They are not interchangeable:

| Parameter | Opcode | Safe to drive from a live signal? |
|---|---|---|
| Rate | `0xAB` | **No.** A frequency chosen for a client is a clinical decision, not a display variable. Entrainment at a drifting rate is not entrainment. |
| Dark duty | `0xAC` | **No.** Every write restarts the strobe phase — see §2. |
| Depth | `0xA2` | **Yes.** It scales a waveform already in flight without disturbing it. |

Set the rate and duty once, before you enter strobe mode. Then move `0xA2` and
nothing else.

---

## 2. Why not duty (`0xAC`)

`0xAC` does not scale the flash, it **re-times** it. From the firmware:

```c
case 0xAC:                          /* Set strobe duty cycle */
    if (arg < 10) arg = 10;
    if (arg > 90) arg = 90;
    strobe_duty_pct = arg;
    strobe_update();                /* ← recomputes the cycle */
    prefs_set_u8(KEY_STROBE_DUTY, arg);
    break;
```

`strobe_update()` calls `strobe_start()` while a strobe is running, and
`strobe_start()` begins with:

```c
strobe_acc = 0;                     /* DDS phase accumulator */
```

**The phase accumulator is zeroed.** A write landing mid-cycle truncates the cycle
in progress, and the wearer sees a brief extra dark pulse. The cost is the *write*,
not the size of the change — so a 1 % adjustment glitches exactly as much as a
40 % one, and rationing updates to a few per second does not smooth it. It turns a
continuous stutter into a staircase with a lurch on every tread.

`0xAC` also writes NVS on every call. Modulating it from a live signal is a flash
erase several times a second, for the life of the session.

Use `0xAC` when the *timing itself* is the intervention, set it once, and expect it
to be coarse.

## 3. Why depth (`0xA2`) is the right one — with one caveat

`0xA2` sets `brightness`, which the 100 µs ISR reads live as the dark-phase level:

```c
effective_duty = is_dark ? brightness : 0;
```

There is no `strobe_update()` in its handler, so **nothing is re-timed**: the flash
keeps its rhythm and only its depth changes. That is exactly what you want.

> ### ⚠️ `0xA2` is persisted to NVS
>
> ```c
> case 0xA2:
>     if (arg > 100) arg = 100;
>     brightness = arg;
>     prefs_set_u8(KEY_BRIGHTNESS, arg);      /* ← NVS write */
>     ESP_LOGI(TAG, "Brightness: %d%% (saved)", brightness);
> ```
>
> It is glitch-free, not free. **Rate-limit it.** A deadband of ~2 points and a
> floor of ~200 ms between writes (≈5 Hz) is the shipping choice and looks
> continuous. Do not stream it at your full sample rate.
>
> It also persists, so it changes the depth of the magnet-tap standalone programs
> and of any later breathe program until something rewrites it.

## 4. `0xA0` and `0xA1` will not help you here

The on-device glide (`0xA0`) and slew cap (`0xA1`) apply to **commanded static
output only** — `0xA5` and the 1-byte opacity write. They do not touch strobe or
breathe waveforms. Setting them and expecting the strobe depth to glide between
your writes is a natural mistake and it silently does nothing.

**Consequence: for strobe feedback, smoothing is entirely your responsibility.**
Which is where most of the trouble actually comes from.

---

## 5. The smoothing trap (this is the part that bites)

Everyone writes this filter:

```python
ema = alpha * value + (1 - alpha) * ema          # ← don't
```

It is wrong in a way that does not announce itself, and both products that hit the
pulsing bug had this exact line.

**`alpha` is per *sample*, not per *unit time*.** So:

1. **It is sample-rate dependent.** The same `alpha` is four times the smoothing at
   64 Hz that it is at 16 Hz. Change your polling rate — or run on a machine where
   packets arrive at a different cadence — and the feel of the feedback changes,
   with nothing in your settings to explain it.
2. **The numbers are much smaller than they look.** `alpha = 0.6` reads like "quite
   a lot of averaging". At 16 Hz its time constant is:

   ```
   tau = -1 / (fs * ln(1 - alpha)) = -1 / (16 * ln(0.4)) = 0.068 s
   ```

   **68 milliseconds.** That is not smoothing, that is a wire. A shipping product
   had this as its default under a help string promising "about a second", and even
   its heaviest preset only reached 217 ms.

Against a feedback signal moving 20 points per second — ordinary for a band-ratio
statistic on a short epoch — none of that attenuates anything. The flash depth
tracks the raw signal and the client reads it as the glasses malfunctioning.

### Do this instead

Express the filter as a **time constant in seconds**, and derive the coefficient
from the *measured* interval between samples:

```python
import math, time

class Smoother:
    """First-order low-pass with a time constant in SECONDS.

    tau is the time to cover ~63% of a step, and it means the same thing
    whatever rate your samples happen to arrive at.
    """

    # A measured interval can also be a dropped packet or a clock step. Letting
    # a ten-second gap through would advance the output most of the way to the
    # new value in one sample - which is itself a visible pulse, from the very
    # filter that is meant to prevent them.
    MIN_DT = 1.0 / 512.0
    MAX_DT = 0.5

    def __init__(self, tau_s: float = 0.8):
        self.tau = max(0.0, tau_s)
        self._y = None
        self._t = None

    def reset(self):
        """Call on session start, stream restart, or reconnect.

        Without this the next session opens by fading from wherever the last
        one ended.
        """
        self._y = None
        self._t = None

    def update(self, value: float) -> float:
        now = time.monotonic()
        dt = (now - self._t) if self._t is not None else 0.0
        self._t = now
        if self._y is None or self.tau <= 0.0:
            self._y = float(value)                  # first sample is taken whole
        else:
            dt = min(max(dt, self.MIN_DT), self.MAX_DT)
            self._y += (1.0 - math.exp(-dt / self.tau)) * (value - self._y)
        return self._y
```

Do not measure the interval once and cache it. Sample delivery is rarely as
regular as the nominal rate — one product's poller ran at 16 Hz but only produced
a new value when the source published one, so its "16 Hz" was an upper bound.

### Choosing tau

| tau | Feel | Use when |
|---|---|---|
| `0.2 s` | Close to raw | The signal is already averaged upstream (long epochs, a smoothed band ratio) |
| `0.8 s` | Good default | Most real-time work |
| `2.0 s` | Drifts rather than flickers | Long sessions, comfort, a noisy channel |
| `0` | No filter | You are certain your source is clean, or you are debugging |

Replayed against a real 3-minute session whose depth moved ≥10 points in 38 % of
seconds: `0.8 s` roughly halves the movement, `2.0 s` cuts it to a third.

### Is this too slow for operant conditioning?

The reflex objection is that the reinforcement literature wants feedback inside
about half a second, so a 0.8 s filter must break the contingency. It does not,
and the distinction is worth being precise about:

- **Dead time** — nothing moves at all until an interval expires — *does* break
  credit assignment. A 20-second epoch pacer, or a rationed write interval, is
  dead time.
- **Lag** — a first-order filter — has its maximum rate of change at *t = 0*. It
  starts responding on the very next sample, and larger excursions cross a visible
  threshold sooner. There is no interval during which nothing happens.

The honest counter-argument is that the depth deadband in §3 *does* introduce a
small amount of real dead time, because the filtered value has to travel ~2 points
before anything is written. Size your travel range (§6) so that a typical success
moves more than that, and check it against a real recording rather than assuming.

The other half of the argument: feedback that is uncorrelated from one second to
the next is not tighter contingency. It is no contingency, delivered promptly. The
client cannot discover what they did to earn it.

---

## 6. All the knobs

| Knob | Range | Default | What it is |
|---|---|---|---|
| `rate_hz` | 0.5–50 Hz | protocol-dependent | `0xAB`, deci-Hz form. Never driven by the signal. |
| `duty_pct` | 10–90 | 50 | `0xAC`, dark fraction. Set once, before `0xA6`. |
| `depth_min_pct` | 0–100 | 5 | `0xA2` at the reward end. **Not zero** — see below. |
| `depth_max_pct` | 0–100 | 50 | `0xA2` at the penalty end. |
| `smoothing_tau_s` | 0–5 s | 0.8 | §5. In seconds, always. |
| `write_deadband` | 1–5 pts | 2 | Skip writes smaller than this. |
| `write_interval_s` | 0.1–1.0 s | 0.2 | Floor between `0xA2` writes. NVS wear. |

### Never let the travel floor reach zero

A flash that stops entirely when the client is doing badly reads as **hardware that
has failed**, not as feedback — and it silently converts proportional feedback into
a threshold. Keep `depth_min_pct` above zero, around 5 %, so a clear lens still
pulses faintly.

The span between floor and ceiling *is* the sensitivity. A narrow span makes the
whole session feel the same; a wide one makes every change obvious. There is no
separate sensitivity number to keep consistent with them.

### If you auto-scale to the client, bias-correct your variance

Normalising a signal onto the lens with `target + (v - mean)/(2*sigma) * sens` is
a common pattern. If `sigma` comes from an EMA of squared deviations started at
zero, it holds only `1 - (1-a)^n` of the true variance after `n` samples — so it is
too small, and the map drives the lens **harder than the gain you configured**.
Measured on a shipping product: 2.4× ten seconds in, settling at 1.33×.

```python
weight = 1.0 - (1.0 - a) ** n
sigma = max(FLOOR, (var_ema / max(weight, 1e-9)) ** 0.5)
```

And if you freeze the calibration at the end of a window, note that an EMA whose
coefficient is `1/(window * rate)` has covered only 63 % of its target after
exactly one window. Run the estimator ~3× faster than the window you freeze on.

---

## 7. Complete example

```python
import asyncio, math, struct, time
from bleak import BleakClient

EDGE_CTRL = "0000ff01-0000-1000-8000-00805f9b34fb"


class StrobeFeedback:
    """Carries a 0-100 feedback signal on the strobe's depth.

    Rate and duty are staged BEFORE the mode is entered, so the first flash the
    wearer sees is the one that was asked for rather than whatever the firmware
    had left over from the last client.
    """

    def __init__(self, client, *, rate_hz=10.0, duty_pct=50,
                 depth_min_pct=5, depth_max_pct=50, smoothing_tau_s=0.8,
                 write_deadband=2, write_interval_s=0.2):
        self.c = client
        self.rate_hz = max(0.5, min(50.0, rate_hz))
        self.duty_pct = max(10, min(90, duty_pct))
        self.lo = max(0, min(100, depth_min_pct))
        self.hi = max(self.lo, min(100, depth_max_pct))
        self.smoother = Smoother(smoothing_tau_s)
        self.deadband = write_deadband
        self.interval = write_interval_s
        self._sent = -1
        self._last_t = 0.0
        self._running = False

    async def _cmd(self, *payload):
        await self.c.write_gatt_char(EDGE_CTRL, bytes(payload), response=True)

    async def start(self):
        dhz = round(self.rate_hz * 10)
        await self.c.write_gatt_char(
            EDGE_CTRL, struct.pack("<BH", 0xAB, dhz), response=True)
        await self._cmd(0xAC, self.duty_pct)
        await self._cmd(0xA2, self.lo)          # stage depth before entering
        await self._cmd(0xA6, 0x00)             # enter strobe
        self.smoother.reset()
        self._sent, self._last_t, self._running = self.lo, time.monotonic(), True

    async def feed(self, value: float):
        """Call as often as you like with your 0-100 feedback value."""
        if not self._running:
            return
        smoothed = self.smoother.update(max(0.0, min(100.0, value)))
        depth = self.lo + round(smoothed * (self.hi - self.lo) / 100.0)
        now = time.monotonic()
        if (abs(depth - self._sent) >= self.deadband
                and (now - self._last_t) >= self.interval):
            await self._cmd(0xA2, depth)
            self._sent, self._last_t = depth, now

    async def stop(self):
        """A static write is how the firmware is told to stop the program, so
        this both exits strobe mode and leaves the wearer with a clear lens."""
        self._running = False
        await self._cmd(0xA5, 0)


async def main(address):
    async with BleakClient(address) as client:
        fb = StrobeFeedback(client, rate_hz=13.5, depth_min_pct=5,
                            depth_max_pct=50, smoothing_tau_s=0.8)
        await fb.start()
        try:
            while True:
                await fb.feed(your_signal_0_to_100())
                await asyncio.sleep(1 / 30)
        finally:
            await fb.stop()          # never leave a wearer strobing
```

### JavaScript

```js
class Smoother {
  constructor(tauS = 0.8) { this.tau = Math.max(0, tauS); this.y = null; this.t = null; }
  reset() { this.y = null; this.t = null; }
  update(v) {
    const now = performance.now() / 1000;
    let dt = this.t === null ? 0 : now - this.t;
    this.t = now;
    if (this.y === null || this.tau <= 0) { this.y = v; return this.y; }
    dt = Math.min(Math.max(dt, 1 / 512), 0.5);
    this.y += (1 - Math.exp(-dt / this.tau)) * (v - this.y);
    return this.y;
  }
}

async function startStrobe(chCtrl, { rateHz = 10, dutyPct = 50, depthMin = 5 }) {
  const dhz = Math.round(Math.max(0.5, Math.min(50, rateHz)) * 10);
  await sendCtrlCommand(chCtrl, 0xAB, new Uint8Array([dhz & 0xff, dhz >> 8]));
  await sendCtrlCommand(chCtrl, 0xAC, new Uint8Array([Math.max(10, Math.min(90, dutyPct))]));
  await sendCtrlCommand(chCtrl, 0xA2, new Uint8Array([depthMin]));
  await sendCtrlCommand(chCtrl, 0xA6, new Uint8Array([0x00]));
}
```

---

## 8. Safety

> ### ⚠️ Photosensitive epilepsy
>
> Flashing light can trigger seizures. **The 15–25 Hz band is the highest-risk
> region** and the range most likely to be chosen for beta training.
>
> - Screen clients before any strobe session, and ask about family history.
> - Provide a stop control the wearer can reach themselves, and tell them it is
>   there before you start.
> - Stop immediately on any report of discomfort, disorientation, or aura.
> - Do not leave a strobe running unattended, and always send `[0xA5, 0]` on the
>   way out — including in your error paths. A crashed app leaves the lens
>   rendering its last commanded mode; see
>   [§2.5](bluetooth-protocol.md#25-reconnection) and set the `0xA3` failsafe.

## 9. Checklist

- [ ] Rate and duty written **once**, before `0xA6`
- [ ] Only `0xA2` driven by the signal
- [ ] Smoothing is a **time constant in seconds**, from the measured interval
- [ ] Smoother reset on session start and reconnect
- [ ] `0xA2` writes rate-limited (deadband + interval) — it hits NVS
- [ ] Depth floor above zero
- [ ] `[0xA5, 0]` on every exit path, including exceptions
- [ ] `[0xA3, 0x01]` failsafe set at connect
- [ ] Client screened for photosensitivity

---

*See also: [protocol reference §4.6.6](bluetooth-protocol.md#466-strobe) for the raw
opcodes, [§4.6.1](bluetooth-protocol.md#461-continuous-opacity-feedback--the-biofeedback-pattern)
for continuous tint feedback (where `0xA0` glide **does** apply and client-side
smoothing is optional).*
