# EDGE Glasses BLE API Reference

**Firmware Version:** 4.15.7+
**Last Updated:** July 2026

This is the **wire / opcode reference** for the Narbis Edge glasses: every BLE command, its bytes, ranges, and on-device behavior. You can drive the glasses directly from raw BLE with only this document.

- Prefer a library? The **[Python SDK](../python-SDK/README.md)** and **[JS SDK](../js-SDK/README.md)** wrap every opcode here in a named method — the opcode ↔ method map is in [§ SDK method map](#sdk-method-map).
- Need the status/PPG notification frames, the OTA flow, or the earclip? Those live in the full **[protocol doc](../docs/bluetooth-protocol.md)** — this file is glasses-control only.

---

## Contents

1. [Connection](#connection)
2. [The ≥ 2-byte rule](#critical-writes-must-be--2-bytes)
3. [Legacy opacity command (1 byte)](#legacy-opacity-command-single-byte)
4. [Command table](#commands)
5. [Real-time feedback — the screen-dimmer pattern](#real-time-feedback--the-screen-dimmer-pattern)
6. [Lens config knobs (fw ≥ 4.15.7)](#lens-config-knobs-fw--4157)
7. [0xBA — breathe sync](#0xba--breathe-sync-fw--4155)
8. [Session & power](#session--power)
9. [Reconnection & disconnect behavior](#reconnection--disconnect-behavior)
10. [Standalone programs (no app)](#standalone-programs-no-app-required)
11. [Lens physics & timing](#lens-physics--timing)
12. [Defaults](#defaults)
13. [SDK method map](#sdk-method-map)
14. [Legacy — on-board coherence (unused)](#legacy--on-board-coherence-unused)
15. [Examples](#examples)

---

## Connection

| Parameter | Value |
|-----------|-------|
| Device Name | `Narbis_Edge` (exact match — filter on this; the service UUID is **not** in the advertisement) |
| Service UUID | `0x00FF` (16-bit) or `000000ff-0000-1000-8000-00805f9b34fb` (128-bit) |
| Control Characteristic | `0xFF01` (read + write) — all commands below |
| Other Characteristics | `0xFF02` OTA data, `0xFF03` status/notify, `0xFF04` PPG stream — out of scope here, see the [protocol doc](../docs/bluetooth-protocol.md) |
| MTU | 247 (requested) |
| Pairing / Bonding | None |
| Connection interval | 20–30 ms requested (the host OS BLE stack grants it; you can't set it) |
| Supervision timeout | 32 s |
| Idle Teardown | **2 minutes** with no client connected → full radio power-down; tap the magnet to re-arm advertising |
| Simultaneous Clients | **1** — advertising stops on connect; a second central cannot discover or connect while another client holds the link |
| Write Type | Write with response (`0xFF01` does **not** expose write-without-response) |

**No NACKs:** the firmware never rejects a write. Out-of-range arguments are silently clamped or dropped — validate values client-side. To detect a failure, use a write timeout.

**Serialize your writes:** keep exactly one write to `0xFF01` in flight at a time. Overlapping GATT writes fail on WinRT ("operation already in progress") and Web Bluetooth alike.

---

## CRITICAL: writes must be ≥ 2 bytes

Any **1-byte write is interpreted as the legacy opacity command** (see below). Every opcode command therefore MUST be at least 2 bytes long — pad argument-less opcodes with a zero byte, e.g. `[0xA6, 0x00]`, `[0xA7, 0x00]`. A bare `[0xA6]` or `[0xA7]` is treated as an opacity write of 166 or 167, not a command.

---

## Legacy Opacity Command (Single Byte)

Any single-byte write sets lens opacity directly:

| Byte | Result |
|------|--------|
| `0x00` | Clear (0% opacity, fully transparent) |
| `0x01` - `0xFE` | Proportional opacity |
| `0xFF` | Dark (100% opacity, fully opaque) |

**Mapping:** Linear `0-255` → `0-100%` static duty.

**Behavior:** Stops any running mode (breathe/strobe/static) and holds the opacity until the next command. Not persisted. This is the cheapest continuous-feedback path — see [Real-time feedback](#real-time-feedback--the-screen-dimmer-pattern). (`0xA5` below does the same thing with an explicit opcode and a 0–100 scale.)

---

## Commands

| Opcode | Name | Arg | Persisted (NVS) | Notes |
|--------|------|-----|-----------------|-------|
| *(1 byte)* | Legacy opacity | 0-255 → 0-100% static duty | no | Stops current mode |
| `0xA0` | Lens smoothing | 0-255 (EMA τ × 10 ms; 0 = off) | yes | fw ≥ 4.15.7. On-device glide between commanded static targets — see [Lens config](#lens-config-knobs-fw--4157) |
| `0xA1` | Lens max transition rate | 0-100 %/100 ms (0 = unlimited) | yes | fw ≥ 4.15.7. Hard slew cap on commanded static, applied after `0xA0` — see [Lens config](#lens-config-knobs-fw--4157) |
| `0xA2` | Brightness | 0-100 % | yes | Sets + persists the SAME `brightness` variable `0xA5` writes — NOT a ceiling clamping `0xA5` (a later `0xA5` overwrites it); immediate; does not change mode; also the breathe depth/amplitude |
| `0xA3` | On-disconnect behavior | `0x00` continue (default) / `0x01` fail clear | yes | fw ≥ 4.15.7 — see [Reconnection](#reconnection--disconnect-behavior) |
| `0xA4` | Session duration | 1-60 min | yes | Device auto-sleeps at session end — see [Session & power](#session--power) |
| `0xA5` | Static mode + duty | 0-100 % | no | Enters static mode at the given duty. The main real-time-feedback command |
| `0xA6` | Start strobe mode | ignored (send `0x00`) | no | Uses stored frequency/duty (`0xAB`/`0xAC`); needs an active session |
| `0xA7` | Sleep now | ignored (send `0x00`) | no | Immediate deep sleep |
| `0xAB` | Strobe frequency | 1-50 Hz | yes | 3-byte form `[0xAB, lo, hi]` sets 0.1 Hz precision (u16 LE deci-Hz), fw ≥ 4.14.41 |
| `0xAC` | Strobe duty | 10-90 % | yes | Dark-phase duty |
| `0xB0` | Start breathe mode | `0x00` breathe / `0x01` breathe+strobe | no | Uses stored breathe parameters; `0x01` requires fw ≥ 4.15.6. A **bare `[0xB0]` is not breathe** — it's a 1-byte opacity write (~69%); always send the 2-byte form |
| `0xB1` | Breathe rate | 1-30 BPM (integer) | yes | Boot default 6 BPM |
| `0xB2` | Breathe inhale ratio | 10-90 % | yes | Portion of the cycle spent inhaling |
| `0xB3` | Breathe hold-top | 0-50 (× 100 ms) | yes | Hold at full dark |
| `0xB4` | Breathe hold-bottom | 0-50 (× 100 ms) | yes | Hold at clear |
| `0xB5` | Breathe waveform | 0 sine / 1 linear | yes | |
| `0xBA` | Breathe sync | `[cycle_ms:u16 LE][inhale_pct:u8]` | no | Fractional-rate phase lock — see [§0xBA](#0xba--breathe-sync-fw--4155) |
| `0xBF` | Factory reset | ignored (send `0x00`) | — | Resets all persisted settings to defaults |
| `0xA8`/`0xA9`/`0xAA`/`0xAD` | OTA | — | — | Firmware update flow; see the [protocol doc](../docs/bluetooth-protocol.md) |

> **Argument clamping is not universal.** Most opcodes clamp out-of-range args into range, but a few **ignore** the write instead: `0xB7`/`0xB8` (legacy coherence) drop out-of-range args, and `0xE0` rejects the whole write on a validation failure. None of them NACK.

---

## Real-time feedback — the screen-dimmer pattern

The core integration: map any protocol's feedback value to lens tint — a wearable screen dimmer. Dim when the trainee is out of condition, clear when in condition.

**Proportional feedback** (a dimmer that tracks a continuous signal):

1. `[0xA4, minutes]` once — a session guard so auto-sleep doesn't end the session early ([Session & power](#session--power)).
2. On each feedback update, map your value → duty 0-100 and write `[0xA5, duty]`.
3. **Rate:** ~12 Hz (production-proven). 20 Hz is the ceiling. Decimate a faster signal — you don't need a write per sample. This is a *smoothing* cadence, not a reward-latency floor.
4. **Coalesce:** skip the write if `duty` is unchanged — the lens holds its state.
5. **One write in flight:** never overlap writes (see [Connection](#connection)).

**Discrete reward** (reinforce the instant a contingency is met — operant conditioning): don't wait for your streaming loop's next tick — send `[0xA5, duty]` (typically `[0xA5, 0]` = clear) immediately. `0xA5` applies with no smoothing by default, so reward latency is just ~20–60 ms BLE transport + a < 100 ms lens switch, bounded by your upstream analysis window, not the streaming rate. (If you have enabled `0xA0`/`0xA1`, those deliberately stretch the transition — leave them off for minimum-latency rewards.)

> Both patterns are built into the SDKs' `FeedbackStream` (`feed()`/`feed_reward()` for the stream, `reward_event()` for immediate discrete rewards). See the [Python](../python-SDK/README.md) / [JS](../js-SDK/README.md) READMEs.

---

## Lens config knobs (fw ≥ 4.15.7)

Three persisted knobs shape how commanded **static** transitions render. All default `0` = pre-4.15.7 behavior (snap, no cap, freeze-on-disconnect), so they're safe to send unconditionally — older firmware ignores them. They affect **only commanded static duty** (`0xA5`, the 1-byte opacity write, and the `0xA3` fail-clear); strobe, breathe, the standalone programs, and the coherence-lens are untouched.

### `0xA0` — Lens smoothing (EMA)

`[0xA0, τ]`, τ in ×10 ms units (0-255 → 0-2.55 s), 0 = off. The firmware EMA-glides between commanded static targets instead of snapping. Use it to smooth a low-rate or lossy feedback stream: steps from a 10–12 Hz stream, RF-retransmit gaps, and rate drops all render as continuous motion.

**Tuning:** τ ≈ 1–2× your write period. A 12 Hz stream (~83 ms) → τ arg 8–16 (≈ 80–160 ms).

### `0xA1` — Lens max transition rate (slew cap)

`[0xA1, rate]`, rate in %/100 ms (0-100, 0 = unlimited). A hard slew limit on commanded static transitions, applied **after** the `0xA0` glide — a safety envelope guaranteeing the lens can't snap even if a host streams garbage. `40` ≈ full-scale in 250 ms (the breathe engine's own internal limit). Breathe/strobe waveforms are unaffected.

### `0xA3` — On-disconnect behavior

Covered in [Reconnection](#reconnection--disconnect-behavior).

---

## 0xBA — Breathe Sync (fw ≥ 4.15.5)

Phase-locks the breathe engine to an external pacer, and is the only way to get **fractional** breathing rates (`0xB1` is integer-BPM). 4 bytes on the wire:

| Byte | Value |
|------|-------|
| 0 | `0xBA` |
| 1-2 | `cycle_ms` (u16, little-endian) — exact breath cycle length in ms; valid 2000-30000, silently clamped |
| 3 | `inhale_pct` (u8) — inhale ratio 10-90 %, silently clamped |

**Behavior:** restarts the breathe cosine at the instant of the write and sets the exact cycle length in milliseconds.

**Boundary-only rule:** send `0xBA` only at the breath-cycle boundary, never mid-breath — the waveform restarts on write, so a mid-breath sync causes a visible jump.

**Per-breath keep-alive:** the sync auto-expires **2 cycles** after the last `0xBA` write, reverting to the stored integer `0xB1` BPM. To hold a fractional rate you **must** re-send `0xBA` every breath at the boundary — sending it once silently reverts within two breaths. (The expiry is time-based, so it applies while connected too.)

Older firmware ignores `0xBA` — safe to send unconditionally.

---

## Session & power

The glasses run a **session timer**. It starts at device wake/boot (or sensor plug-in), runs for the session duration, and at expiry the device **enters deep sleep** — the lens goes dark and BLE drops.

- Default duration is **30 minutes**; `0xA4` sets 1–60 min and is **persisted**, so a previous client's value survives across connects.
- Writing `0xA4` sets the total but does **not** restart the clock. At session start, write `[0xA4, minutes]` ≥ your planned session.
- Sessions longer than 60 min require a mid-session re-wake (magnet tap).
- `0xA7` sleeps immediately; a magnet close ≥ 5 s also sleeps.

> There is no low-battery or session-remaining readout over the control characteristic, and the Edge exposes no Battery Service and no DIS — so **glasses battery level is not exposed over BLE, and firmware version is not available via a GATT read.** (The firmware version *is* pushed over BLE as a text status frame on `0xFF03` — leading type byte `0xF1`, `"Narbis fw v…"` — whenever a client subscribes to the status characteristic; and the `0xF3` health frame on `0xFF03` carries `uptime_s`, which shares the session clock's origin. See the protocol doc.)

---

## Reconnection & disconnect behavior

- **CCCD subscriptions are lost** on disconnect — re-subscribe on every reconnect.
- **Lens state is NOT lost.** By default the lens **freezes** at its last commanded output across a disconnect: a crashed app leaves the last tint in place (e.g. fully dark) until reconnect, a magnet action, or session-expiry sleep. So before an *intentional* disconnect, send `[0xA5, 0x00]` (clear) or `[0xA7, 0x00]` (sleep) so the wearer isn't left dark.
- **Fail-clear (fw ≥ 4.15.7):** write `[0xA3, 0x01]` once (persisted) and the glasses instead stop any strobe and drop to a clear static lens on any disconnect (riding the `0xA0` glide if set). The failsafe fires when the firmware declares the link dead, bounded by the **32 s** supervision timeout — a crashed app can still leave the wearer dark for up to ~32 s, so the pre-disconnect clear write remains good practice.
- **Re-assert on reconnect.** After reconnecting, re-subscribe to CCCDs and, if you were pacing a fractional breathe rate, resume sending `0xBA` (its 2-cycle time-based sync will have lapsed). Lens mode and static duty are held in RAM and, by default (`0xA3` = 0), **persist** across a plain disconnect (see above) — but they are lost on sleep/reboot, so re-asserting your full lens setup on reconnect is still good practice. **NVS-persisted** opcodes survive both disconnects and reboots: `0xA2`, `0xA4`, `0xAB`, `0xAC`, `0xB1`–`0xB5` (plus fw ≥ 4.15.7 `0xA0`, `0xA1`, `0xA3`).
- No application keep-alive is needed while connected — the 2-minute teardown applies only when *no* client is connected.

---

## Standalone Programs (no app required)

A short magnet tap (0.15-4 s) on the temple cycles the on-board programs. The lens signals a program change with N slow fade-dark pulses. These render from the same NVS-persisted parameters the opcodes write (breathe rate/shape `0xB1`/`0xB2`/`0xB5`, strobe `0xAB`/`0xAC`), so values your app persists change them.

| Program | Behavior (factory defaults) |
|---------|----------|
| 1 — BREATHE | 6 BPM sine, lens tint follows the waveform (boot default) |
| 2 — BREATHE+STROBE | 10 Hz strobe whose dark-phase duty is modulated by the breathing waveform |
| 3 — STROBE | Plain 10 Hz strobe |

A long magnet close (≥ 5 s) enters deep sleep. **Magnet gestures stay live while an app is connected** (only OTA suspends them) — a mid-session tap overwrites the app's lens mode, so watch for it if it matters to your protocol.

---

## Lens physics & timing

- **Opacity floor:** duty 1-100 % maps to raw PWM 265-1023 (fw ≥ 4.15.4) — a perceptual floor that skips the invisible low range. Duty 0 is fully clear.
- **Switching time (electrochromic cell):** Ton (transparent→dark) 2.5-40 ms, Toff (dark→transparent) 2.5-50 ms; < 100 ms all modes, slower when cold. Fast enough that the lens is not the latency bottleneck for feedback.
- `0xA5` applies on-device immediately by default (the breathe slew limiter is breathe-mode only) — unless you've set the `0xA0`/`0xA1` knobs.

---

## Defaults

| Parameter | Default |
|-----------|---------|
| Session duration | 30 min (persisted) |
| Breathe rate | 6 BPM (boot default, program 1) |
| Standalone strobe | 10 Hz |
| Lens smoothing (`0xA0`) | 0 (off / snap) |
| Lens slew cap (`0xA1`) | 0 (unlimited) |
| On-disconnect (`0xA3`) | 0 (freeze / continue) |

Parameters marked "persisted" survive sleep and power cycles (NVS); `0xBF` restores factory values.

---

## SDK method map

| Opcode | Python (`edge_glasses`) | JavaScript (`edge-glasses`) |
|--------|--------------------------|------------------------------|
| *(1 byte)* opacity | `set_opacity(0-255)` / `clear()` / `dark()` | `setOpacity` / `clear` / `dark` |
| `0xA0` | `set_lens_smoothing(ms)` | `setLensSmoothing(ms)` |
| `0xA1` | `set_lens_max_rate(pct_per_100ms)` | `setLensMaxRate(pctPer100ms)` |
| `0xA2` | `set_brightness(0-100)` | `setBrightness` |
| `0xA3` | `set_disconnect_behavior(fail_clear)` | `setDisconnectBehavior(failClear)` |
| `0xA4` | `set_duration(1-60)` | `setDuration` |
| `0xA5` | `set_static(0-100)` | `setStatic` |
| `0xA6` | `start_strobe(hz?, duty_pct?)` | `startStrobe` |
| `0xA7` | `sleep()` | `sleep` |
| `0xAB`/`0xAC` | `set_strobe_frequency` / `set_strobe_duty` | `setStrobeFrequency` / `setStrobeDuty` |
| `0xB0`–`0xB5` | `start_breathe(...)` | `startBreathe({...})` |
| `0xBA` | `sync_breath(cycle_ms, inhale_pct)` | `syncBreath(cycleMs, inhalePct)` |
| `0xBF` | `factory_reset()` | `factoryReset()` |
| real-time stream | `start_feedback_stream()` → `FeedbackStream` | `startFeedbackStream()` → `FeedbackStream` |

---

## Legacy — On-board Coherence (unused)

The firmware retains an on-board coherence/biofeedback pipeline: `0xB6` pulse-on-beat, `0xB7` PPG program 0-3, `0xB8` coherence difficulty, `0xB9` adaptive pacer, `0xCA` external-IBI injection, `0xCB` HR source, `0xD0` detector reset, `0xE0` coherence tuning. These are functional but no longer used — all processing is app-side now. The Edge↔earclip BLE relay is compile-disabled on stock builds. Full details: [protocol doc §4.8](../docs/bluetooth-protocol.md#48-legacy-on-board-coherence-pipeline-unused).

---

## Examples

**Configure a custom strobe, then start it:**
```
Write: [0xA2, 0x64]              # brightness 100%
Write: [0xAB, 0x0A]              # strobe 10 Hz
Write: [0xAC, 0x32]              # strobe duty 50%
Write: [0xA4, 0x0A]             # 10-min session
Write: [0xA6, 0x00]              # start strobe
```

**Real-time feedback (screen dimmer), raw wire:**
```
Write: [0xA4, 0x3C]             # 60-min session guard
loop at ~12 Hz:
  Write: [0xA5, duty]           # duty 0 (clear) .. 100 (dark); skip if unchanged
```

**Paced breathing with a fractional rate:**
```
Write: [0xB1, 0x06]             # 6 BPM (integer fallback)
Write: [0xB2, 0x28]             # 40% inhale ratio
Write: [0xB5, 0x00]             # sine waveform
Write: [0xB0, 0x00]             # start breathe mode
# then, once per breath at the cycle boundary (required to hold the rate):
Write: [0xBA, 0x10, 0x27, 0x28]  # 0x2710 = 10000 ms cycle, 0x28 = 40% inhale
```

**Smooth a low-rate stream + fail-clear on disconnect (fw ≥ 4.15.7):**
```
Write: [0xA0, 0x0A]             # 100 ms smoothing glide
Write: [0xA3, 0x01]             # clear the lens if the app link drops
```
