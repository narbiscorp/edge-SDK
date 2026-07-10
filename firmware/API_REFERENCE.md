# EDGE Glasses BLE API Reference

**Firmware Version:** 4.15.6+
**Last Updated:** July 2026

---

## Connection

| Parameter | Value |
|-----------|-------|
| Device Name | `Narbis_Edge` (exact match — filter on this) |
| Service UUID | `0x00FF` (16-bit) or `000000ff-0000-1000-8000-00805f9b34fb` (128-bit) |
| Control Characteristic | `0xFF01` (read + write) — all commands below |
| Other Characteristics | `0xFF02` OTA data, `0xFF03` status/notify, `0xFF04` PPG stream — out of SDK scope, see the [protocol doc](../docs/bluetooth-protocol.md) |
| MTU | 247 |
| Pairing/Bonding | None |
| Idle Teardown | **2 minutes** with no client connected → full radio power-down; tap the magnet to re-arm advertising |
| Supervision Timeout | 32 s |
| Simultaneous Clients | **1** — advertising stops on connect; a second central cannot discover or connect while another client holds the link |
| Write Type | Write with response |

**No NACKs:** the firmware never rejects a write. Out-of-range arguments are silently clamped or dropped — validate values client-side.

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

**Behavior:** Stops any running mode (breathe/strobe/static) and holds the opacity until the next command. Not persisted. Safe to stream at up to ~20 Hz for continuous feedback.

---

## Commands

| Opcode | Name | Arg | Persisted (NVS) | Notes |
|--------|------|-----|-----------------|-------|
| *(1 byte)* | Legacy opacity | 0-255 → 0-100% static duty | no | Stops current mode |
| `0xA2` | Brightness | 0-100 % | yes | Global ceiling; takes effect immediately, does not change mode |
| `0xA4` | Session duration | 1-60 min | yes | Device auto-sleeps when the session ends (default 30 min; persisted; timer runs from device wake — see the protocol doc's session-auto-sleep note) |
| `0xA5` | Static mode + duty | 0-100 % | no | Enters static mode at the given duty |
| `0xA6` | Start strobe mode | ignored (send `0x00`) | no | Uses stored frequency/duty (`0xAB`/`0xAC`) |
| `0xA7` | Sleep now | ignored (send `0x00`) | no | Immediate deep sleep |
| `0xAB` | Strobe frequency | 1-50 Hz | yes | |
| `0xAC` | Strobe duty | 10-90 % | yes | Dark-phase duty |
| `0xB0` | Start breathe mode | `0x00` breathe / `0x01` breathe+strobe | no | Uses stored breathe parameters; `0x01` requires fw ≥ 4.15.6 |
| `0xB1` | Breathe rate | 1-30 BPM (integer) | yes | Boot default 6 BPM |
| `0xB2` | Breathe inhale ratio | 10-90 % | yes | Portion of the cycle spent inhaling |
| `0xB3` | Breathe hold-top | 0-50 (× 100 ms) | yes | Hold at full dark |
| `0xB4` | Breathe hold-bottom | 0-50 (× 100 ms) | yes | Hold at clear |
| `0xB5` | Breathe waveform | 0 sine / 1 linear | yes | |
| `0xBA` | Breathe sync | `[cycle_ms:u16 LE][inhale_pct:u8]` | no | See dedicated section below |
| `0xBF` | Factory reset | ignored (send `0x00`) | — | Resets persisted settings |
| `0xA8`/`0xA9`/`0xAA`/`0xAD` | OTA | — | — | Firmware update flow; not an SDK method — see the [protocol doc](../docs/bluetooth-protocol.md) |

**Examples:**
```
Write: [0xA2, 0x64]              # brightness 100%
Write: [0xAB, 0x0A]              # strobe 10 Hz
Write: [0xAC, 0x32]              # strobe duty 50%
Write: [0xA6, 0x00]              # start strobe
Write: [0xB1, 0x06]              # breathe 6 BPM
Write: [0xB0, 0x01]              # start breathe+strobe
Write: [0xA7, 0x00]              # sleep now
```

---

## 0xBA — Breathe Sync (fw ≥ 4.15.5)

Phase-locks the breathe engine to an external pacer. 4 bytes on the wire:

| Byte | Value |
|------|-------|
| 0 | `0xBA` |
| 1-2 | `cycle_ms` (u16, little-endian) — exact breath cycle length in ms |
| 3 | `inhale_pct` (u8) — inhale ratio 10-90 % |

**Behavior:** restarts the breathe cosine at the instant of the write and sets the exact cycle length in milliseconds — this is the only way to get fractional breathing rates (`0xB1` is integer-BPM only).

**Boundary-only rule:** send `0xBA` only at the breath-cycle boundary, never mid-breath — the waveform restarts on write, so a mid-breath sync causes a visible jump.

**Auto-expiry:** the sync expires 2 cycles after the last `0xBA` write, reverting to the stored integer BPM. Send once per breath to keep lock.

Older firmware ignores `0xBA` — safe to send unconditionally.

**Example:** 10-second cycle (6 BPM), 40% inhale:
```
Write: [0xBA, 0x10, 0x27, 0x28]   # 0x2710 = 10000 ms, 0x28 = 40%
```

---

## Standalone Programs (no app required)

A short magnet tap (0.15-4 s) on the temple cycles the on-board programs. The lens signals a program change with N slow fade-dark pulses:

| Program | Behavior |
|---------|----------|
| 1 — BREATHE | 6 BPM sine, lens tint follows the waveform (boot default) |
| 2 — BREATHE+STROBE | 10 Hz strobe whose dark-phase duty is modulated by the breathing waveform |
| 3 — STROBE | Plain 10 Hz strobe |

A long magnet close (≥ 5 s) enters deep sleep.

---

## Defaults

| Parameter | Default |
|-----------|---------|
| Breathe rate | 6 BPM (boot default, program 1) |
| Standalone strobe | 10 Hz |

Parameters marked "persisted" above survive sleep and power cycles (NVS); `0xBF` restores factory values.

---

## Lens Physics

Duty 1-100% maps to raw PWM 265-1023 (fw ≥ 4.15.4) — a perceptual floor that skips the invisible low range. Duty 0 is fully clear.

---

## Legacy — On-board Coherence (unused)

The firmware retains an on-board coherence/biofeedback pipeline: `0xB6` pulse-on-beat, `0xB7` PPG program 0-3, `0xB8` coherence difficulty, `0xB9` adaptive pacer, `0xCA` external-IBI injection, `0xCB` HR source, `0xD0` detector reset, `0xE0` coherence tuning. These are functional but no longer used — all processing is app-side now. The Edge↔earclip BLE relay is compile-disabled on stock builds. Full details: [protocol doc §4.8](../docs/bluetooth-protocol.md#48-legacy-on-board-coherence-pipeline-unused).

---

## Example: Paced Breathing Session

Configure breathe at 6 BPM with a 40% inhale, start it, then phase-lock:

```
Write: [0xB1, 0x06]               # 6 BPM
Write: [0xB2, 0x28]               # 40% inhale ratio
Write: [0xB5, 0x00]               # sine waveform
Write: [0xB0, 0x00]               # start breathe mode
# then, once per breath, at the cycle boundary:
Write: [0xBA, 0x10, 0x27, 0x28]   # sync: 10000 ms cycle, 40% inhale
```

The `0xB1`/`0xB2`/`0xB5` values persist, so a reconnecting client only needs `[0xB0, 0x00]` to resume the same pattern.
