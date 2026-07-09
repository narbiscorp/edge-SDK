# EDGE Smart Glasses

Open-source smart LCD glasses for meditation, neurofeedback, and biofeedback applications.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-ESP32-green.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![TypeScript](https://img.shields.io/badge/typescript-5.0+-blue.svg)

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
| **BCI Research** | Motor imagery, SSVEP, P300 paradigms |

### Why EDGE?

- **Open Protocol** — Simple BLE API, no vendor lock-in
- **Low Latency** — 20 Hz update rate for real-time feedback
- **Cross-Platform SDKs** — Python for research, JS for web apps
- **Sensor Agnostic** — Works with any biosignal source via LSL/brainflow
- **Research Ready** — Compatible with OpenBCI, Muse, Polar, and lab equipment

## Hardware

| Component | Specification |
|-----------|---------------|
| MCU | ESP32-PICO-D4 |
| Connectivity | Bluetooth Low Energy 4.0+ |
| Lens Control | PWM-driven LCD opacity |
| Power | Li-ion battery, ~8hr active |
| Sleep Current | ~16 µA |

## Repositories

| Location | Description |
|----------|-------------|
| [Protocol reference](https://narbiscorp.github.io/edge-earclip/docs/bluetooth-protocol.md/) | Published firmware BLE protocol doc (full protocol, OTA, legacy opcodes) |
| [python-SDK/](python-SDK/) | Python SDK with OpenBCI/Muse/Polar examples |
| [js-SDK/](js-SDK/) | JavaScript/TypeScript SDK for web apps |

## Quick Start

The glasses advertise as **`Narbis_Edge`**. If they don't show up in a scan, tap the magnet on the temple — the radio powers down after 2 minutes with no client connected.

### Python
```bash
pip install edge-glasses
```

```python
from edge_glasses import Glasses
import asyncio

async def main():
    async with Glasses() as glasses:
        await glasses.set_opacity(128)        # 50% dark
        await glasses.session_meditate(10)    # 10-min breathe session, 6 BPM

asyncio.run(main())
```

### JavaScript
```bash
npm install edge-glasses
```

```typescript
import { Glasses } from 'edge-glasses';

const glasses = new Glasses();
await glasses.connect();
await glasses.setOpacity(128);
await glasses.sessionMeditate(10);
```

Or drive the breathe engine directly:

```python
await glasses.start_breathe(bpm=6, inhale_pct=40)   # paced breathing, on-board
await glasses.sync_breath(10000, 40)                # optional phase-lock, once per breath
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
| [openbci_feedback.py](python-SDK/examples/openbci_feedback.py) | EEG alpha neurofeedback |
| [muse_eeg.py](python-SDK/examples/muse_eeg.py) | Meditation/focus training |
| [polar_hrv.py](python-SDK/examples/polar_hrv.py) | HRV coherence training |
| [lsl_integration.py](python-SDK/examples/lsl_integration.py) | Any LSL-compatible source |
| [Integration Guide](python-SDK/docs/INTEGRATION_GUIDE.md) | Full setup documentation |

## BLE Protocol

Simple byte-based protocol for direct integration. Service `0x00FF`, control characteristic `0xFF01`, write with response.

| Command | Bytes | Description |
|---------|-------|-------------|
| Opacity (legacy) | `[0x00-0xFF]` | Single byte = lens opacity 0-255; stops current mode |
| Brightness | `[0xA2, pct]` | Max brightness 0-100% (persisted) |
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

Full protocol (including OTA and legacy opcodes): [Protocol reference](https://narbiscorp.github.io/edge-earclip/docs/bluetooth-protocol.md/) · [API Reference](firmware/API_REFERENCE.md)

### Connection quirks

- Advertised name is exactly `Narbis_Edge` — filter on it.
- **2-minute teardown:** with no client connected, the radio powers down fully after 2 minutes. Tap the magnet on the temple to re-arm advertising.
- **No NACKs:** the firmware silently clamps or drops out-of-range arguments. Validate values client-side (the SDKs do).
- MTU 247, no pairing/bonding, 32 s supervision timeout.

## Features

### Standalone programs

The glasses work without any app. A short magnet tap (0.3-4 s) on the temple cycles through three sensor-free programs; the lens signals the new program with N slow fade-dark pulses:

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

Update opacity for smooth neurofeedback — keep it at or below 20 Hz:

```python
while True:
    alpha = get_eeg_alpha()  # Your processing
    await glasses.set_opacity(int(alpha * 255))
    await asyncio.sleep(0.05)
```

For breathing entrainment, prefer the on-board breathe engine (configure, start, optionally `syncBreath()` once per breath at the cycle boundary) over streaming per-tick opacity.

## Documentation

- [API Reference](firmware/API_REFERENCE.md) — Complete BLE command reference
- [Protocol deep-dive](https://narbiscorp.github.io/edge-earclip/docs/bluetooth-protocol.md/) — Full firmware protocol, OTA, legacy opcodes
- [Integration Guide](python-SDK/docs/INTEGRATION_GUIDE.md) — OpenBCI, Muse, Polar, LSL setup
- [Python SDK Docs](python-SDK/README.md)
- [JavaScript SDK Docs](js-SDK/README.md)

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
