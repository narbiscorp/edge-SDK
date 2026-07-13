# EDGE Glasses Python SDK — API Reference

**Firmware 4.15.6+ — July 2026**
**SDK version:** 2.0.0

This document maps every Python SDK method to the exact bytes it writes over BLE.
For the full protocol (OTA, status/notify, PPG stream), see the
[BLE protocol deep-dive](../../docs/bluetooth-protocol.md).

---

## Connection

| Parameter | Value |
|-----------|-------|
| Advertised name | `Narbis_Edge` (exact match — the SDK filters on this) |
| Service UUID | `0x00FF` (`000000ff-0000-1000-8000-00805f9b34fb`) |
| Control characteristic | `0xFF01` (`0000ff01-0000-1000-8000-00805f9b34fb`) — read + write, all commands |
| Other characteristics | `0xFF02` OTA data, `0xFF03` status/notify, `0xFF04` PPG stream — out of SDK scope, see protocol doc |
| BLE stack | NimBLE; MTU 247; TX 0 dBm; no pairing/bonding |
| Write type | Write with response |
| Idle teardown | **2 minutes** with no client connected → full radio power-down; magnet tap re-arms advertising |
| Supervision timeout | 32 s |
| Simultaneous clients | **1** — advertising stops on connect; a second central cannot discover or connect while another client holds the link |
| NACKs | None — firmware silently clamps/drops bad args; the SDK validates client-side |

---

## The ≥ 2-byte write rule

A **1-byte write is the legacy opacity command** (0-255 → 0-100% static duty).
Therefore every opcode command must be at least 2 bytes — the SDK pads
argument-less opcodes to `[opcode, 0x00]`. Only `set_opacity()` intentionally
sends a single byte.

---

## Method → Wire Mapping

### Opacity (legacy single-byte write)

| Method | Wire bytes | Notes |
|--------|-----------|-------|
| `set_opacity(value)` | `[value]` (1 byte, 0-255) | 0-255 → 0-100% static duty; stops current mode; not persisted. Streamable at ~12 Hz (recommended, production-proven); ~20 Hz is only a tolerated ceiling. |
| `clear()` | `[0x00]` | Fully transparent |
| `dark()` | `[0xFF]` | Fully opaque |

### Parameters

| Method | Wire bytes | Range (SDK clamps) | Persisted (NVS) |
|--------|-----------|--------------------|-----------------|
| `set_brightness(percent)` | `[0xA2, pct]` | 0-100% | yes |
| `set_duration(minutes)` | `[0xA4, min]` | 1-60 min (auto-sleep at end; default 30 min; persisted; timer runs from device wake — see protocol doc session-auto-sleep note) | yes |
| `set_static(duty)` | `[0xA5, duty]` | 0-100% | no |
| `set_strobe_frequency(hz)` | `[0xAB, hz]` | 1-50 Hz | yes |
| `set_strobe_duty(percent)` | `[0xAC, pct]` | 10-90% | yes |

### Modes

| Method | Wire bytes | Notes |
|--------|-----------|-------|
| `start_strobe(hz=None, duty_pct=None)` | optional `[0xAB, hz]`, `[0xAC, pct]`, then `[0xA6, 0x00]` | 0xA6 arg is ignored (SDK sends 0). Omitted params keep stored values. |
| `start_breathe(bpm=..., inhale_pct=..., hold_top_ms=..., hold_bottom_ms=..., waveform=..., with_strobe=False)` | writes only the params given, then `[0xB0, arg]` | `arg` = `0x00` breathe / `0x01` breathe+strobe (fw ≥ 4.15.6) |
| `sync_breath(cycle_ms, inhale_pct=40)` | `[0xBA, cycle_lo, cycle_hi, inhale_pct]` | `cycle_ms` as u16 little-endian; 4 bytes total on wire. See boundary rule below. |

`start_breathe` parameter writes:

| Parameter | Wire bytes | Range (SDK clamps) | Persisted (NVS) |
|-----------|-----------|--------------------|-----------------|
| `bpm` | `[0xB1, bpm]` | 1-30 BPM (integer — use `sync_breath` for fractional rates) | yes |
| `inhale_pct` | `[0xB2, pct]` | 10-90% | yes |
| `hold_top_ms` | `[0xB3, ms // 100]` | 0-5000 ms → 0-50 units of 100 ms | yes |
| `hold_bottom_ms` | `[0xB4, ms // 100]` | 0-5000 ms → 0-50 units of 100 ms | yes |
| `waveform` | `[0xB5, w]` | `Waveform.SINE` = 0, `Waveform.LINEAR` = 1 | yes |

### Power / Maintenance

| Method | Wire bytes | Notes |
|--------|-----------|-------|
| `sleep()` | `[0xA7, 0x00]` | Deep sleep now; arg ignored (padded) |
| `factory_reset()` | `[0xBF, 0x00]` | Reset NVS settings; arg ignored (padded) |
| `send_command(opcode, payload=None)` | `[opcode, ...payload]` padded to ≥ 2 B | Low-level escape hatch |

### Preset Sessions (fixed-parameter)

The firmware no longer ramps any parameter over the session; presets just
configure the renderer and set the auto-sleep duration.

| Method | Sequence sent |
|--------|---------------|
| `session_relax(duration=10)` | `[0xA2, 100]`, `[0xB1, 5]`, `[0xB5, 0]`, `[0xB0, 0]`, `[0xA4, min]` — 5 BPM sine, brightness 100 |
| `session_meditate(duration=10)` | `[0xB1, 6]`, `[0xB5, 0]`, `[0xB0, 0]`, `[0xA4, min]` — 6 BPM sine (device default) |
| `session_focus(duration=10)` | `[0xAB, 12]`, `[0xB1, 8]`, `[0xB0, 1]`, `[0xA4, min]` — breathe+strobe, 12 Hz, 8 BPM |
| `session_sleep(duration=15)` | `[0xB1, 4]`, `[0xB5, 0]`, `[0xB0, 0]`, `[0xA4, min]` — 4 BPM sine |

---

## 0xBA Breathe Sync — boundary rule

`sync_breath(cycle_ms, inhale_pct=40)` (firmware ≥ 4.15.5):

- Restarts the breathe cosine **at the instant of the write** and sets the EXACT
  cycle length in milliseconds — this is how you get fractional breathing rates
  (`0xB1` is integer-BPM only).
- **Send only at the breath-cycle boundary (inhale onset), never mid-breath** —
  the waveform restarts immediately on receipt.
- Auto-expires **2 cycles** after the last sync, reverting to the stored
  integer-BPM rate — re-send once per breath to stay locked.
- Ignored by older firmware; always safe to send.

---

## Command Summary

| Opcode | Name | Arg | Persisted (NVS) | SDK method |
|--------|------|-----|-----------------|------------|
| *(1 byte)* | Legacy opacity | 0-255 → 0-100% static duty; stops current mode | no | `set_opacity` |
| `0xA2` | Brightness | 0-100% | yes | `set_brightness` |
| `0xA4` | Session duration | 1-60 min (auto-sleep at end; default 30 min; timer runs from device wake — see protocol doc session-auto-sleep note) | yes | `set_duration` |
| `0xA5` | Static mode + duty | 0-100% | no | `set_static` |
| `0xA6` | Start strobe mode | arg ignored (send 0) | no | `start_strobe` |
| `0xA7` | Sleep now | arg ignored (send 0) | no | `sleep` |
| `0xAB` | Strobe frequency | 1-50 Hz | yes | `set_strobe_frequency` |
| `0xAC` | Strobe duty | 10-90% | yes | `set_strobe_duty` |
| `0xB0` | Start breathe mode | `0x00` breathe / `0x01` breathe+strobe (fw ≥ 4.15.6) | no | `start_breathe` |
| `0xB1` | Breathe rate | 1-30 BPM (integer) | yes | `start_breathe(bpm=...)` |
| `0xB2` | Breathe inhale ratio | 10-90% | yes | `start_breathe(inhale_pct=...)` |
| `0xB3` | Breathe hold-top | 0-50 (×100 ms) | yes | `start_breathe(hold_top_ms=...)` |
| `0xB4` | Breathe hold-bottom | 0-50 (×100 ms) | yes | `start_breathe(hold_bottom_ms=...)` |
| `0xB5` | Breathe waveform | 0 sine / 1 linear | yes | `start_breathe(waveform=...)` |
| `0xBA` | Breathe sync | `[cycle_ms:u16 LE][inhale_pct:u8]` | no | `sync_breath` |
| `0xBF` | Factory reset | arg ignored (send 0) | — | `factory_reset` |
| `0xA8`/`0xA9`/`0xAA`/`0xAD` | OTA | — | — | not an SDK method — see [protocol doc](../../docs/bluetooth-protocol.md) |

---

## Lens physics note

Duty 1-100% maps to raw 265-1023 — a perceptual floor so 1% is already visible
(firmware ≥ 4.15.4). Duty 0 is fully clear.

---

## Legacy / unused opcodes

`0xB6` pulse-on-beat, `0xB7` PPG program 0-3, `0xB8` coherence difficulty,
`0xB9` adaptive pacer, `0xCA` external-IBI injection, `0xCB` HR source,
`0xD0` detector reset, `0xE0` coherence tuning — the on-board coherence pipeline.
Functional but unused: all processing is app-side now. The Edge↔earclip BLE relay
is compile-disabled on stock builds. See
[protocol doc §4.8](../../docs/bluetooth-protocol.md#48-legacy-on-board-coherence-pipeline-unused)
for the full story. The SDK does not expose these.
