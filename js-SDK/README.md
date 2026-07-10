# EDGE Glasses JavaScript SDK

Control EDGE Smart LCD Glasses over Web Bluetooth.

The glasses are a **display**: your app computes its biofeedback signal (EEG alpha,
HRV, GSR, ...) and drives the lens by commanding the firmware's breathe / static /
strobe renderer. All coherence / HRV processing runs app-side.

Requires glasses firmware 4.15.6 or later. Full protocol details:
[Bluetooth protocol deep-dive](../docs/bluetooth-protocol.md).

## Installation

```bash
npm install edge-glasses
```

## Quick Start

```typescript
import { Glasses } from 'edge-glasses';

const glasses = new Glasses();

// Must be called from a user gesture (button click)
document.getElementById('connect')?.addEventListener('click', async () => {
  await glasses.connect();
  await glasses.setOpacity(128);           // 50% dark, static
  await glasses.startBreathe({ bpm: 6 });  // on-board breathe engine
});
```

## Browser Support

Web Bluetooth is supported in:
- Chrome 56+ (desktop & Android)
- Edge 79+
- Opera 43+
- Samsung Internet 6.2+

**Not supported:** Firefox, Safari, iOS browsers

`connect()` must be called from a user gesture (e.g. a click handler) — the browser
blocks the device chooser otherwise.

## Connection Notes

- The SDK filters on the exact advertised name **`Narbis_Edge`**, with the control
  service (`0x00FF`) as a fallback filter.
- **Idle teardown:** after 2 minutes with no client connected, the glasses power the
  radio down completely and stop advertising. If the device doesn't appear in the
  chooser, **tap the magnet to the temple** briefly to re-arm advertising.
- **Single control characteristic:** every command in this SDK goes over one
  characteristic (`0xFF01`), written with response. Other characteristics (OTA data,
  status/notify, PPG stream) exist but are out of SDK scope — see the
  [protocol doc](../docs/bluetooth-protocol.md).
- **No NACKs:** the firmware never rejects a command. Out-of-range arguments are
  silently clamped or dropped on the device, so the SDK validates and clamps
  everything client-side before sending.
- No pairing/bonding is required.

## Standalone Programs (no app needed)

The glasses also run sensor-free on-board programs, cycled by a short magnet tap
(0.15–4 s) on the temple:

1. **BREATHE** — 6 BPM sine, lens tint follows the waveform (boot default)
2. **BREATHE+STROBE** — 10 Hz strobe with dark-phase duty modulated by the breathing waveform
3. **STROBE** — plain 10 Hz strobe

A long magnet close (≥ 5 s) puts the glasses into deep sleep. On program change the
lens gives N slow fade-dark pulses to indicate the program number.

## Usage

### Basic Control

```typescript
import { Glasses } from 'edge-glasses';

const glasses = new Glasses();
await glasses.connect();

// Simple opacity control (legacy 1-byte write; stream at ~12 Hz recommended, <= 20 Hz tolerated)
await glasses.clear();           // Fully transparent
await glasses.setOpacity(128);   // 50% dark
await glasses.dark();            // Fully opaque

// Static hold at a duty cycle
await glasses.setStatic(75);     // Hold at 75%

// Sleep
await glasses.sleep();

// Disconnect
glasses.disconnect();
```

### Breathe Mode

```typescript
import { Glasses, Waveform } from 'edge-glasses';

// Start the on-board breathe engine. Only options you pass are written;
// the rest keep their persisted values on the device.
await glasses.startBreathe({
  bpm: 6,                    // 1-30 BPM (integer)
  inhalePct: 40,             // 10-90 % of the cycle
  holdTopMs: 500,            // 0-5000 ms, 100 ms resolution
  holdBottomMs: 0,
  waveform: Waveform.Sine,   // Sine (0) or Linear (1)
});

// Breathe+strobe: strobe dark-phase duty follows the breathing waveform
await glasses.startBreathe({ bpm: 8, withStrobe: true });
```

### Phase-locking to Your Own Pacer (`syncBreath`)

If your app runs its own breath pacer, phase-lock the glasses to it with
`syncBreath()` — it restarts the breathe waveform at the instant of the write and
sets the *exact* cycle length in ms (fractional rates, unlike the integer-BPM
setting). **Send it only at the breath-cycle boundary** (start of inhale), once per
breath — never mid-breath, or the lens visibly jumps. The sync auto-expires 2 cycles
after the last write and the engine falls back to its integer-BPM rate.

```typescript
// Your pacer wraps at the start of each inhale:
onBreathCycleStart(() => {
  glasses.syncBreath(9500, 40);  // 9.5 s cycle (~6.3 BPM), 40% inhale
});
```

### Strobe Mode

```typescript
await glasses.startStrobe(10, 50);  // 10 Hz, 50% duty
await glasses.startStrobe();        // reuse persisted frequency/duty

// Or set parameters without starting:
await glasses.setStrobeFrequency(12);  // 1-50 Hz (persisted)
await glasses.setStrobeDuty(40);       // 10-90 % (persisted)
```

### Timed Sessions & Presets

Presets are **fixed-parameter** — the firmware no longer ramps frequency or
breathing over the session. Each preset configures the renderer, sets the duration
(the glasses auto-sleep when it elapses), and starts the mode.

```typescript
await glasses.sessionRelax(15);    // 5 BPM sine breathe, full brightness
await glasses.sessionMeditate(10); // 6 BPM sine breathe (device default)
await glasses.sessionFocus(10);    // breathe+strobe, 12 Hz strobe, 8 BPM
await glasses.sessionSleep(20);    // 4 BPM sine breathe

// Or compose your own:
await glasses.setBrightness(80);
await glasses.setDuration(12);
await glasses.startBreathe({ bpm: 5 });
```

### Real-time Biofeedback

Map a continuous signal to lens opacity at ~12 Hz (production-proven rate; ~20 Hz is the tolerated ceiling):

```typescript
// Update opacity in real-time (e.g., from sensor data)
function updateFromSensor(value: number) {
  // value is 0-1 from your sensor
  const opacity = Math.floor(value * 255);
  glasses.setOpacity(opacity);
}

// Example: 12 Hz update loop
setInterval(() => {
  const sensorValue = getSensorReading();  // Your function
  updateFromSensor(sensorValue);
}, 50);
```

For breathing entrainment, do **not** stream per-tick opacity to draw a waveform —
configure and start the on-board breathe engine, and optionally phase-lock it with
`syncBreath()` once per breath at the cycle boundary.

## React Example

```tsx
import { useState, useCallback } from 'react';
import { Glasses } from 'edge-glasses';

const glasses = new Glasses();

function GlassesControl() {
  const [connected, setConnected] = useState(false);
  const [opacity, setOpacity] = useState(0);

  const handleConnect = useCallback(async () => {
    try {
      await glasses.connect();
      setConnected(true);
    } catch (err) {
      console.error('Connection failed:', err);
    }
  }, []);

  const handleOpacityChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseInt(e.target.value);
    setOpacity(value);
    if (connected) {
      await glasses.setOpacity(value);
    }
  }, [connected]);

  return (
    <div>
      {!connected ? (
        <button onClick={handleConnect}>Connect Glasses</button>
      ) : (
        <div>
          <input
            type="range"
            min="0"
            max="255"
            value={opacity}
            onChange={handleOpacityChange}
          />
          <span>{Math.round(opacity / 255 * 100)}%</span>
        </div>
      )}
    </div>
  );
}
```

## API Reference

### Connection

| Method | Description |
|--------|-------------|
| `connect()` | Connect to glasses (requires user gesture) |
| `disconnect()` | Disconnect from glasses |
| `isConnected` | Check connection status |
| `deviceName` | Get connected device name |

### Simple Control

| Method | Description |
|--------|-------------|
| `setOpacity(0-255)` | Static lens opacity (legacy 1-byte write; stream at ~12 Hz, ≤ 20 Hz ceiling) |
| `clear()` | Fully transparent |
| `dark()` | Fully opaque |
| `setStatic(0-100)` | Static mode at duty cycle % |
| `sleep()` | Enter deep sleep |

### Settings (persisted on device)

| Method | Description |
|--------|-------------|
| `setBrightness(0-100)` | Lens level / breathe depth % (persisted; same firmware variable `setStatic` writes — not a ceiling) |
| `setDuration(1-60)` | Session length in minutes (auto-sleep at end) |
| `setStrobeFrequency(1-50)` | Strobe frequency in Hz |
| `setStrobeDuty(10-90)` | Strobe dark-phase duty % |

### Modes

| Method | Description |
|--------|-------------|
| `startStrobe(hz?, dutyPct?)` | Start strobe mode, optionally setting parameters first |
| `startBreathe(options)` | Start breathe or breathe+strobe mode (see `BreatheOptions`) |
| `syncBreath(cycleMs, inhalePct=40)` | Phase-lock breathe engine; boundary-only, auto-expires after 2 cycles |

### Preset Sessions (fixed-parameter)

| Method | Description |
|--------|-------------|
| `sessionRelax(min=10)` | 5 BPM sine breathe, full brightness |
| `sessionMeditate(min=10)` | 6 BPM sine breathe (device default) |
| `sessionFocus(min=10)` | Breathe+strobe: 12 Hz strobe, 8 BPM |
| `sessionSleep(min=15)` | 4 BPM sine breathe |

### Low-level / Maintenance

| Method | Description |
|--------|-------------|
| `sendCommand(opcode, payload?)` | Raw opcode write (padded to ≥ 2 bytes) |
| `factoryReset()` | Restore persisted settings to factory defaults |

### Legacy: on-board coherence pipeline

Older firmware opcodes for the on-board coherence/PPG pipeline (pulse-on-beat, PPG
programs, coherence difficulty/tuning, external-IBI injection, HR source, detector
reset) still exist and function, but are **no longer used by Narbis apps** — all
biofeedback processing runs app-side now, and this SDK does not wrap them. The
Edge↔earclip BLE relay is also compile-disabled on stock builds. See
[protocol doc §4.8](../docs/bluetooth-protocol.md#48-legacy-on-board-coherence-pipeline-unused)
for the full story.

## Migrating from v1

v2.0.0 is a breaking release matching firmware 4.15.6+. Removed methods and their
replacements:

| v1 method | v2 replacement |
|-----------|----------------|
| `setStrobe(startHz, endHz)` | `setStrobeFrequency(hz)` / `startStrobe(hz, duty)` — no in-session frequency ramp anymore |
| `setBreathing(inh, holdIn, exh, holdOut)` | `startBreathe({ bpm, inhalePct, holdTopMs, holdBottomMs })` |
| `resume()` | `startStrobe()` (the opcode now means "start strobe mode") |
| `startSession(config)` | Presets (`sessionRelax` etc.) or explicit `setBrightness` + `setDuration` + `startBreathe`/`startStrobe` |
| `hold(duty)` | `setStatic(duty)` |

Also changed: the advertised device name is now `Narbis_Edge` (was `Smart_Glasses`),
and all argument-less commands are sent as 2 bytes on the wire (v1's bare 1-byte
opcode writes are misread as opacity commands by current firmware).

## Integration Examples

### OpenBCI / brainflow

See the Python SDK for brainflow integration. For web apps, use a WebSocket bridge:

```typescript
const ws = new WebSocket('ws://localhost:8765');

ws.onmessage = async (event) => {
  const { alpha } = JSON.parse(event.data);
  await glasses.setOpacity(Math.floor(alpha * 255));
};
```

### Muse (via muse-js)

```typescript
import { MuseClient } from 'muse-js';
import { Glasses } from 'edge-glasses';

const muse = new MuseClient();
const glasses = new Glasses();

await muse.connect();
await glasses.connect();

muse.eegReadings.subscribe(reading => {
  // Process EEG and update glasses
  const alpha = calculateAlphaPower(reading);
  glasses.setOpacity(Math.floor(alpha * 255));
});
```

### HRV (e.g. Polar RR intervals)

Compute metrics app-side (RMSSD etc.) and drive the lens with `setOpacity()` /
`setStatic()`, or pace breathing with `startBreathe()` + `syncBreath()`.

## TypeScript

Full TypeScript support with type definitions included.

```typescript
import { Glasses, BreatheOptions, Waveform } from 'edge-glasses';

const options: BreatheOptions = {
  bpm: 6,
  inhalePct: 40,
  waveform: Waveform.Sine,
};

await glasses.startBreathe(options);
```

## License

MIT License - see LICENSE file.
