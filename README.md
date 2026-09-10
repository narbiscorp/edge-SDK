# EDGE Smart Glasses

Open-source smart LCD glasses for meditation, neurofeedback, and biofeedback applications.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-ESP32-green.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![TypeScript](https://img.shields.io/badge/typescript-5.0+-blue.svg)
![C++](https://img.shields.io/badge/C%2B%2B-17-blue.svg)
![C#](https://img.shields.io/badge/.NET-standard%202.0%20%7C%208.0-blue.svg)

## What is EDGE?

EDGE glasses feature LCD lenses that dynamically change opacity via Bluetooth. An open platform for biofeedback, neurofeedback, and human-computer interaction research.

**Architecture:** all signal processing runs app-side — the glasses are a display. Your app computes its feedback signal (EEG alpha, HRV coherence, GSR, anything) and drives the lens by commanding the firmware's breathe / static / strobe renderer. The firmware still ships a legacy on-board coherence pipeline (sensor-driven PPG programs), but it is unused by current apps and not part of the SDK API.

### Applications

| Domain | Use Case |
|--------|----------|
| **Meditation** | Guided breathing with visual pacing and feedback |
| **EEG Neurofeedback** | Alpha/theta training, focus enhancement, relaxation |
| **HRV Biofeedback** | Heart rate variability coherence training |
| **EMG Biofeedback** | Muscle tension awareness, relaxation training |
| **EOG Integration** | Eye movement-triggered states, blink detection |
| **fNIRS** | Hemodynamic response feedback, cognitive load |
| **rPPG** | Camera-based heart rate, stress monitoring |
| **GSR/EDA** | Arousal-based feedback, stress response |
| **Respiration** | Breathing rate entrainment, paced breathing |
| **Evoked Potentials** | Lens as the visual *stimulator* — SSVEP flicker, transient VEP / P300 flash cues ([example](python-SDK/examples/evoked_potential.py)) |
| **BCI Research** | Motor imagery, SSVEP, P300 paradigms |

### Why EDGE?

- **Open Protocol** — Simple BLE API, no vendor lock-in
- **Low Latency** — ~20–60 ms write-to-lens transport; streams real-time feedback at a configurable 30–50 Hz target
- **Cross-Platform SDKs** — Python for research, JS for web apps, C/C++ and C# for native desktop and clinical software
- **Sensor Agnostic** — Works with any biosignal source via LSL/brainflow
- **Research Ready** — Compatible with OpenBCI, Muse, Polar, and lab equipment

## Hardware

| Component | Specification |
|-----------|---------------|
| MCU | ESP32-PICO-D4 |
| Connectivity | Bluetooth Low Energy 4.0+ |
| Lens Control | PWM-driven electrochromic opacity |
| Lens Switching | Ton 2.5-40 ms, Toff 2.5-50 ms (< 100 ms all modes, slower when cold) |
| Power | Li-ion battery, ~2–4 hr active |
| Sleep Current | ~16 µA |

## Repositories

| Location | Description |
|----------|-------------|
| [Protocol reference](docs/bluetooth-protocol.md) | Standalone BLE protocol reference — both devices, all frames, OTA, legacy opcodes (in this repo) |
| [Strobe feedback guide](docs/strobe-feedback.md) | How to carry a live feedback signal on the strobe without it pulsing dark - read before modulating a running strobe |
| [python-SDK/](python-SDK/) | Python SDK with OpenBCI/Muse/Polar examples |
| [js-SDK/](js-SDK/) | JavaScript/TypeScript SDK for web apps |
| [cpp-SDK/](cpp-SDK/) | C++17 SDK with a flat C API and a Windows (WinRT) Bluetooth transport |
| [csharp-SDK/](csharp-SDK/) | C# / .NET SDK (netstandard2.0, net472, net8.0-windows) |

## Quick Start

The glasses advertise as **`Narbis_Edge`**. If they don't show up in a scan, tap the magnet on the temple — the radio powers down after 2 minutes with no client connected.

**Connect and set opacity — the minimal loop.** One connection, one command: `[0xA5, duty]` sets the lens **0 (clear) → 100 (fully dark)**. Writes must be **≥ 2 bytes** (a 1-byte write is the legacy opacity command) and **write-with-response** on characteristic `0xFF01`.

```python
# Python, raw BLE via bleak
import asyncio
from bleak import BleakClient, BleakScanner

CTRL = "0000ff01-0000-1000-8000-00805f9b34fb"   # control characteristic 0xFF01

async def main():
    dev = await BleakScanner.find_device_by_name("Narbis_Edge")
    async with BleakClient(dev) as c:
        await c.write_gatt_char(CTRL, bytes([0xA4, 60]),  response=True)   # session guard: 60 min
        await c.write_gatt_char(CTRL, bytes([0xA5, 0]),   response=True)   # 0   = clear
        await c.write_gatt_char(CTRL, bytes([0xA5, 100]), response=True)   # 100 = fully dark
        await c.write_gatt_char(CTRL, bytes([0xA5, 50]),  response=True)   # 50  = half

asyncio.run(main())
```

```javascript
// JavaScript, raw Web Bluetooth (must run from a user gesture)
const dev = await navigator.bluetooth.requestDevice({
  filters: [{ name: 'Narbis_Edge' }], optionalServices: [0x00ff],
});
const server = await dev.gatt.connect();
const ch = await (await server.getPrimaryService(0x00ff)).getCharacteristic(0xff01);
await ch.writeValueWithResponse(new Uint8Array([0xA5, 50]));   // 0 clear .. 100 dark
```

Or the SDK one-liner — same wire command, validated and serialized for you:

```python
async with Glasses() as glasses:          # JS: await glasses.setStatic(50)
    await glasses.set_static(50)          # 0 clear .. 100 dark
```

The core integration is **direct tint control — a wearable screen dimmer**. Classic neurofeedback dims the training display when the trainee falls out of condition and clears it when they're in condition; the Edge does the same thing on the lens itself, so it drops into **any protocol** (SMR, alpha/theta, HEG, EMG down-training, HRV…) wherever your software can emit a feedback value.

**How real-time control works.** Open one BLE connection and hold it. Every time your feedback signal updates, write the lens opacity — a single 2-byte command, `set_static(duty)`, where `duty` runs **0 (clear) → 100 (fully dark)**. That's the same 0–100% dim level your on-screen dimmer already computes, so you point the existing signal at the lens instead of the screen. The streaming contract:

- **Rate (proportional feedback):** the firmware applies every commanded static duty on its 10 ms tick (100 Hz internally, no command throttling), so the **BLE link** is the only limit. The glasses request a 20–30 ms connection interval with slave latency 1 (~33–50 connection events/sec). A host that accepts those defaults sustains only **~8–11 writes/sec**; a host that requests throughput-optimized connection parameters (e.g. WinRT `RequestPreferredConnectionParameters(ThroughputOptimized)`, held for the session) reaches **~20 writes/sec** (both measured on real glasses, fw 4.16.2, Windows laptop). The hard ceiling is the connection-event rate; reaching the full **30–50 Hz** band uses firmware write-without-response on `0xFF01`, available on **fw ≥ 4.16.3** (older firmware is write-with-response only). **Recommended: target 30–50 Hz with coalescing on** — the effective rate self-limits to your data rate, and since most sources emit ≤ 16–30 new values/sec, coalesced writes rarely reach the ceiling. Decimate a faster signal (a 256 Hz EEG index, say) — you don't need a write per sample.
- **Coalesce:** skip the write when `duty` hasn't changed since the last one — the lens holds its state, so only send real changes.
- **One in flight:** never overlap writes to the control characteristic (on raw BLE, wait for each write to complete before sending the next). Write-with-response paces throughput for you — the configured rate is a *target*, never a queue.
- **Latency:** each write lands on the lens in ~1–2 connection intervals (**~20–60 ms transport**), then the lens itself switches in **Ton 2.5–40 ms / Toff 2.5–50 ms** (< 100 ms only when cold) — a fast cell, not the bottleneck. By default `set_static()` applies immediately (the breathe slew limiter doesn't touch static); if you enable the optional on-device smoothing / slew cap (`0xA0` / `0xA1`, fw ≥ 4.15.7) those deliberately stretch the transition.

> **Why not 12 or 20 Hz?** Earlier docs cited a "12 Hz" rate and a "20 Hz ceiling." The 12 Hz was only the on-board **breathe pacer** cadence (the reference app's paced-breathing update rate), never a lens-control limit; the "20 Hz ceiling" was a stale, overly-conservative figure with no basis in the protocol. The real limit is the BLE link, described above.

> ### Reward timing / operant conditioning
> A **continuous dimmer** and a **discrete reward** have different latency needs, and the streaming cadence only governs the first. For proportional feedback, 30 Hz updates (~33 ms granularity) sit far below your upstream EEG analysis window (typically 250 ms–1 s+), which dominates the loop. For a **discrete reinforcement** — a reward the instant a contingency is met — don't wait for the next stream tick: fire the write immediately. The reward path is then just **~20–60 ms transport + ~40–50 ms lens switch** (Ton ≤ 40 / Toff ≤ 50 ms; < 100 ms only when cold), with the analysis window as the only larger term. So the reinforcement latency is bounded by your signal processing, **not** by the streaming rate. (The SDK's `reward_event()` does exactly this — see below.)

**The SDKs ship this whole contract as a built-in:** `start_feedback_stream()` returns a `FeedbackStream`. For proportional feedback, call `feed_reward(value)` (0..1, 1 = in condition) or `feed(duty)` (0–100, your dimmer's existing scale) from any callback at any rate — the internal writer handles 30 Hz decimation, coalescing, and write serialization. For a discrete operant reward, call **`reward_event(duty=0, hold_ms=…)`**, which writes immediately (bypassing the tick, preempting the stream) so reinforcement isn't gated by the streaming cadence. The snippets below are complete screen-dimmer replacements; for the hand-rolled loop or the raw-BLE byte sequence, see the [protocol doc quickstart](docs/bluetooth-protocol.md).

### Python
```bash
pip install edge-glasses
```

```python
from edge_glasses import Glasses
import asyncio

async def main():
    async with Glasses() as glasses:
        await glasses.set_duration(60)                 # session guard: no auto-sleep for 60 min
        stream = glasses.start_feedback_stream()       # 30 Hz writer -- coalesces + serializes for you
        your_pipeline.on_update(stream.feed_reward)    # push your 0..1 feedback value, any callback, any rate
        # or stream.feed(duty) with your dimmer's existing 0-100% value
        await asyncio.Event().wait()                   # run until you end the session

asyncio.run(main())
```

### JavaScript
```bash
npm install edge-glasses
```

```typescript
import { Glasses } from 'edge-glasses';

const glasses = new Glasses();
await glasses.connect();                       // must come from a user gesture
await glasses.setDuration(60);                 // session guard
const stream = glasses.startFeedbackStream();  // 30 Hz writer -- coalesces + serializes for you
onFeedback((v) => stream.feedReward(v));       // push your 0..1 feedback value, any callback, any rate
```

### C#
```bash
# Not yet on NuGet - reference the project directly:
dotnet add reference path/to/edge-SDK/csharp-SDK/src/Narbis.EdgeGlasses/Narbis.EdgeGlasses.csproj
```

```csharp
using Narbis.EdgeGlasses;

using var glasses = new Glasses(new WinRtBleTransport());
await glasses.ConnectAsync();
await glasses.SetDurationAsync(60);                       // session guard
FeedbackStream stream = glasses.StartFeedbackStream();    // 30 Hz writer -- coalesces + serializes for you
myPipeline.OnUpdate += v => stream.FeedReward(v);         // push your 0..1 feedback value, any thread, any rate
```

Targets `netstandard2.0`, `net472` and `net8.0-windows`, so it drops into
classic WinForms/WPF applications as readily as into .NET 8.

### C++
```cmake
add_subdirectory(cpp-SDK)
target_link_libraries(my_app PRIVATE edge::glasses)
```

```cpp
#include "edge/feedback_stream.hpp"
#include "edge/winrt_transport.hpp"

edge::WinRtTransport transport;
edge::Glasses glasses(&transport);
glasses.connect();
glasses.set_duration(60);                             // session guard
auto stream = glasses.start_feedback_stream();        // 30 Hz writer
stream->feed_reward(value);                           // 0..1, any thread, any rate
```

### C
```c
#include "edge/edge_glasses.h"

edge_glasses g;
edge_glasses_create_winrt(&g);
edge_glasses_connect(g, 10000);
edge_glasses_set_duration(g, 60);                     /* session guard */
edge_stream s;
edge_glasses_start_feedback_stream(g, 30.0, &s);
edge_stream_feed_reward(s, value);                    /* 0..1, any thread, any rate */
```

Already have your own Bluetooth stack? Both the C and C++ SDKs work as a pure
protocol encoder — `edge_frame_set_static(buf, sizeof buf, 50)` hands you the
exact bytes to write, with no BLE dependency at all. Same for C# via
`Protocol.SetStatic(50)`.

Drop-in example: [screen_dimmer.py](python-SDK/examples/screen_dimmer.py). The on-board breathe engine and fixed-parameter sessions are there when a protocol calls for paced breathing:

```python
await glasses.start_breathe(bpm=6, inhale_pct=40)   # paced breathing, on-board
await glasses.session_meditate(10)                  # or a 10-min preset
```

## Integrations

Works with popular biosignal platforms and research equipment. Your computer runs the SDK, which bridges between sensor data (LSL/BLE/USB) and the glasses (BLE).

```
┌─────────────┐      LSL/USB/BLE      ┌─────────────┐       BLE        ┌─────────────┐
│   Sensor    │ ──────────────────▶   │  Computer   │ ───────────────▶ │   Glasses   │
│ (EEG/HRV/…) │                       │ (Python SDK)│                  │   (ESP32)   │
└─────────────┘                       └─────────────┘                  └─────────────┘
```

### Consumer Devices
| Platform | Signals | Connection |
|----------|---------|------------|
| **OpenBCI** | EEG, EMG, ECG, EOG | Cyton, Ganglion via brainflow |
| **Muse** | EEG (4-ch) | Muse 2, Muse S via muselsl |
| **Polar** | HR, HRV | H10, H9, Verity Sense via BLE |
| **Neurosity** | EEG (8-ch) | Crown via brainflow |
| **BrainBit** | EEG (4-ch) | Via brainflow |

### Research Equipment
| Platform | Signals | Connection |
|----------|---------|------------|
| **LSL** | Any | Lab Streaming Layer protocol |
| **BrainFlow** | EEG, EMG, PPG | 20+ supported boards |
| **NIRx** | fNIRS | Via LSL |
| **Biopac** | EMG, ECG, GSR, Resp | Via LSL |
| **Tobii** | Eye tracking, EOG | Via LSL |

### Examples
| Example | Description |
|---------|-------------|
| [screen_dimmer.py](python-SDK/examples/screen_dimmer.py) | **Wearable screen dimmer** — tint from any protocol's feedback value (threshold or proportional) |
| [openbci_feedback.py](python-SDK/examples/openbci_feedback.py) | EEG alpha neurofeedback |
| [evoked_potential.py](python-SDK/examples/evoked_potential.py) | Lens as SSVEP / VEP / P300 visual stimulator (with LSL markers) |
| [muse_eeg.py](python-SDK/examples/muse_eeg.py) | Meditation/focus training |
| [polar_hrv.py](python-SDK/examples/polar_hrv.py) | HRV coherence training |
| [lsl_integration.py](python-SDK/examples/lsl_integration.py) | Any LSL-compatible source |
| [Integration Guide](python-SDK/docs/INTEGRATION_GUIDE.md) | Full setup documentation |

## BLE Protocol

Simple byte-based protocol for direct integration. Service `0x00FF`, control characteristic `0xFF01`, write with response.

| Command | Bytes | Description |
|---------|-------|-------------|
| Opacity (legacy) | `[0x00-0xFF]` | Single byte = lens opacity 0-255; stops current mode |
| Lens smoothing | `[0xA0, tau]` | EMA glide between streamed targets, τ ×10 ms, 0 = off (persisted; fw 4.15.7+) |
| Lens max rate | `[0xA1, rate]` | Transition-speed cap %/100ms, 0 = unlimited (persisted; fw 4.15.7+) |
| Brightness | `[0xA2, pct]` | Level 0-100% (persisted) — the max-tint / breathe depth that scales breathe & strobe output. On fw ≥ 4.16.2 `0xA5` no longer writes it (clean static); on fw ≤ 4.16.1 `0xA5` and `0xA2` shared one variable |
| On-disconnect | `[0xA3, mode]` | 0 = freeze at last output (default) / 1 = fail clear on link loss (persisted; fw 4.15.7+) |
| Duration | `[0xA4, minutes]` | Session length 1-60 min, auto-sleep at end (persisted) |
| Static | `[0xA5, duty]` | Static mode at duty 0-100% |
| Start strobe | `[0xA6, 0x00]` | Start strobe mode |
| Sleep | `[0xA7, 0x00]` | Enter deep sleep now |
| Strobe frequency | `[0xAB, hz]` | 1-50 Hz (persisted) |
| Strobe duty | `[0xAC, pct]` | 10-90% (persisted) |
| Start breathe | `[0xB0, mode]` | `0x00` breathe / `0x01` breathe+strobe |
| Breathe rate | `[0xB1, bpm]` | 1-30 BPM (persisted) |
| Breathe inhale ratio | `[0xB2, pct]` | 10-90% (persisted) |
| Breathe hold-top | `[0xB3, n]` | 0-50 × 100 ms (persisted) |
| Breathe hold-bottom | `[0xB4, n]` | 0-50 × 100 ms (persisted) |
| Breathe waveform | `[0xB5, w]` | 0 sine / 1 linear (persisted) |
| Breathe sync | `[0xBA, cycle_lo, cycle_hi, inhale_pct]` | Phase-lock; send at breath boundary only |
| Factory reset | `[0xBF, 0x00]` | Reset persisted settings |

**Important:** every opcode command must be at least 2 bytes — a 1-byte write is always interpreted as the legacy opacity command. Pad argument-less opcodes with `0x00`.

Full protocol (including OTA and legacy opcodes): [Protocol reference](docs/bluetooth-protocol.md) · [API Reference](firmware/API_REFERENCE.md)

### Connection quirks

- Advertised name is exactly `Narbis_Edge` — filter on it.
- **2-minute teardown:** with no client connected, the radio powers down fully after 2 minutes. Tap the magnet on the temple to re-arm advertising.
- **No NACKs:** the firmware silently clamps or drops out-of-range arguments. Validate values client-side (the SDKs do).
- MTU 247, no pairing/bonding, 32 s supervision timeout.

## Features

### Standalone programs

The glasses work without any app. A short magnet tap (0.15-4 s) on the temple cycles through three sensor-free programs; the lens signals the new program with N slow fade-dark pulses:

| Program | Behavior |
|---------|----------|
| 1 — Breathe | 6 BPM sine, lens tint follows the waveform (boot default) |
| 2 — Breathe + Strobe | 10 Hz strobe, dark-phase duty modulated by the breathing waveform |
| 3 — Strobe | Plain 10 Hz strobe |

Hold the magnet closed ≥ 5 s for deep sleep.

### Preset sessions

Presets are fixed-parameter: the firmware no longer ramps strobe frequency or grows hold times over a session. Each preset configures the breathe/strobe engine, sets the duration, and starts; the device auto-sleeps when the session ends.

| Preset | Mode | Parameters | Best For |
|--------|------|------------|----------|
| `sessionRelax(10)` | Breathe | 5 BPM sine, brightness 100 | Stress relief, wind-down |
| `sessionMeditate(10)` | Breathe | 6 BPM sine (device default) | General practice |
| `sessionFocus(10)` | Breathe + strobe | 12 Hz strobe, 8 BPM | Concentration, study |
| `sessionSleep(15)` | Breathe | 4 BPM sine | Pre-sleep routine |

### Real-time control

Update opacity for smooth neurofeedback — stream at 30 Hz (the SDK's default `FeedbackStream` rate; see the [rate contract](#quick-start) for why there's no 12/20 Hz limit):

```python
while True:
    alpha = get_eeg_alpha()  # Your processing
    await glasses.set_opacity(int(alpha * 255))
    await asyncio.sleep(1 / 30)  # ~30 Hz
```

For breathing entrainment, prefer the on-board breathe engine (configure, start, optionally `syncBreath()` once per breath at the cycle boundary) over streaming per-tick opacity.

## Documentation

- [API Reference](firmware/API_REFERENCE.md) — Complete BLE command reference
- [Protocol deep-dive](docs/bluetooth-protocol.md) — Full firmware protocol, OTA, legacy opcodes
- [Strobe feedback guide](docs/strobe-feedback.md) — Modulating a running strobe: which opcode to drive, and the smoothing trap
- [Integration Guide](python-SDK/docs/INTEGRATION_GUIDE.md) — OpenBCI, Muse, Polar, LSL setup
- [Python SDK Docs](python-SDK/README.md)
- [JavaScript SDK Docs](js-SDK/README.md)
- [C/C++ SDK Docs](cpp-SDK/README.md)
- [C# SDK Docs](csharp-SDK/README.md)

## Community

- **Issues** — Report bugs or request features in the relevant repo
- **Discussions** — Share projects, ask questions
- **OpenBCI Discord** — Find us in the #hardware channel

## License

MIT License — free for personal and commercial use.

## Contributing

Contributions welcome! See individual repos for contribution guidelines.

---

**Built for the neurofeedback community** 🧠
