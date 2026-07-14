# Narbis Bluetooth Protocol — SDK Integration Reference

> **Audience.** Developers speaking the wire protocol directly — **Python** (via [bleak](https://github.com/hbldh/bleak)) or **JavaScript** (via Web Bluetooth) — whether you're using the edge-SDK libraries or bypassing them. Everything a client needs to talk to the **Narbis Edge** glasses and the **Narbis Earclip** over BLE is in this document.
>
> **Provenance.** Synced to glasses firmware **4.15.7+** and earclip firmware **config v4** — July 2026.
>
> **Scope.** Scanning, connecting, GATT discovery, command writes, notification parsing, driving the lens ([§4.6](#46-driving-the-edge-lens)), OTA firmware update, troubleshooting.
>
> **Architecture — read this first.** All signal processing (coherence, HRV, EEG, anything) runs **app-side**. The glasses are a **display**: your app computes its feedback signal and drives the lens by commanding the firmware's **breathe / static / strobe** renderer ([§4.6](#46-driving-the-edge-lens)). The firmware still ships a legacy on-board coherence pipeline (sensor-driven PPG programs); its opcodes remain on the wire and are documented in [§4.8](#48-legacy-on-board-coherence-pipeline-unused), but current apps do not use it. The glasses also run three sensor-free **standalone programs** cycled by a magnet tap — no app required ([§4.1.1](#411-standalone-programs--magnet-gestures)).
>
> **Pairing/bonding:** neither device requires encryption or bonding.
>
> **SDK docs.** If you'd rather use the shipped libraries: [Firmware API reference](../firmware/API_REFERENCE.md) · [Python SDK API](../python-SDK/docs/API_REFERENCE.md) · [JS SDK](../js-SDK/README.md).

---

## Table of contents

0. [Quickstart — make the lens respond to your signal](#quickstart--make-the-lens-respond-to-your-signal)
1. [The two devices at a glance](#1-the-two-devices-at-a-glance)
2. [Scanning & connecting](#2-scanning--connecting)
3. [Earclip BLE — full reference](#3-earclip-ble--full-reference)
4. [Edge glasses BLE — full reference](#4-edge-glasses-ble--full-reference) ([§4.6.1 Continuous-opacity feedback](#461-continuous-opacity-feedback--the-biofeedback-pattern))
5. [Configuring the earclip](#5-configuring-the-earclip)
6. [OTA — shared between both devices](#6-ota--shared-between-both-devices)
7. [Client gotchas](#7-client-gotchas) ([§7.1 Web Bluetooth](#71-web-bluetooth-gotchas) · [§7.2 Windows / WinRT & Python (bleak)](#72-windows--winrt--python-bleak-gotchas))
8. [Troubleshooting matrix](#8-troubleshooting-matrix)
9. [Reference data](#9-reference-data) ([§9.3 Firmware feature/version matrix](#93-firmware-featureversion-matrix))

---

## Quickstart — make the lens respond to your signal

The minimal path for a third party whose software already produces a feedback value (0..1) and wants the lens to track it. This is the **wearable screen-dimmer** pattern: classic neurofeedback dims the training display when the trainee falls out of condition and clears it when they're in condition — the Edge does the same on the lens itself, so it drops into **any protocol** (SMR, alpha/theta, HEG, EMG down-training, HRV…) with no changes to the protocol logic.

1. **Wake the glasses** — magnet tap on the temple. You get a ~2-minute advertising window.
2. **Scan** for the exact name `Narbis_Edge` (the service UUID is not advertised) and connect.
3. **Discover** service `0x00FF`, characteristic `0xFF01` (Control).
4. **Write `[0xA4, 0x3C]`** — a 60-minute session guard, so the auto-sleep timer doesn't end your session early ([§4.1.2](#412-session-auto-sleep--the-0xa4-timer)).
5. **Loop at ≤ 12 Hz:** map your signal 0..1 → duty 0..100 and write `[0xA5, duty]`. Skip the write if the duty is unchanged, and keep at most one write in flight.
6. *(Optional, fw ≥ 4.15.7)* **Write `[0xA0, 0x0A]`** once — 100 ms of on-device smoothing, so the lens glides between your stream's steps instead of stepping ([§4.3](#43-control-characteristic-0xff01--command-opcodes)). Persisted; older firmware ignores it.

Sanity test: `[0xA5, 0x64]` = fully dark, `[0xA5, 0x00]` = fully clear.

Python (bleak):

```python
import asyncio
from bleak import BleakScanner, BleakClient

CTRL = '0000ff01-0000-1000-8000-00805f9b34fb'

async def run(get_signal):                    # get_signal() → 0.0..1.0
    device = await BleakScanner.find_device_by_name('Narbis_Edge', timeout=15.0)
    async with BleakClient(device) as client:
        await client.write_gatt_char(CTRL, bytes([0xA4, 0x3C]), response=True)  # 60-min session guard
        last = -1
        while True:
            duty = round(get_signal() * 100)  # 0..1 → 0..100
            if duty != last:                  # coalesce: skip unchanged values
                await client.write_gatt_char(CTRL, bytes([0xA5, duty]), response=True)  # 0xFF01 is write-with-response only
                last = duty
            await asyncio.sleep(1 / 12)       # ≤ 12 Hz; the await also keeps writes serialized
```

JavaScript (Web Bluetooth — must run from a click handler, [§7.1](#71-web-bluetooth-gotchas)):

```js
const device = await navigator.bluetooth.requestDevice({
  filters: [{ name: 'Narbis_Edge' }], optionalServices: [0x00ff] });
const svc  = await (await device.gatt.connect()).getPrimaryService(0x00ff);
const ctrl = await svc.getCharacteristic(0xff01);
await ctrl.writeValueWithResponse(new Uint8Array([0xA4, 0x3C]));  // 60-min session guard
let last = -1, busy = false;
setInterval(async () => {
  const duty = Math.round(getSignal() * 100);                     // 0..1 → 0..100
  if (duty === last || busy) return;                              // coalesce; one write in flight
  busy = true; last = duty;
  try { await ctrl.writeValueWithResponse(new Uint8Array([0xA5, duty])); }  // 0xFF01 is write-with-response only (§7.1)
  catch { last = -1; }  // failed write: reset the coalesce key so the next frame retries (matches streamLensDuty)
  finally { busy = false; }
}, 1000 / 12);
```

Read before shipping:

- the **≥ 2-byte write rule** — a 1-byte write is the legacy opacity command ([§4.3](#43-control-characteristic-0xff01--command-opcodes));
- **session auto-sleep** — the glasses deep-sleep when the session timer expires ([§4.1.2](#412-session-auto-sleep--the-0xa4-timer));
- **reconnect / re-assert** — subscriptions don't survive a disconnect, and lens state does (the lens holds its last tint until session expiry); re-subscribe and re-send your lens setup ([§2.5](#25-reconnection)).

---

## 1. The two devices at a glance

| Aspect | Narbis Edge (glasses) | Narbis Earclip |
|---|---|---|
| Advertised name | `Narbis_Edge` (exact match) | `Narbis Earclip <mac>` (prefix match) |
| MCU | ESP32 classic | ESP32-C6 |
| BLE stack | NimBLE | NimBLE |
| BLE roles | **peripheral + central** — the central (earclip relay) is **compile-disabled on stock builds** ([§4.7](#47-the-edge-as-relay)) | peripheral, **multi-central** (up to 3 simultaneous) |
| Primary service | `0x00FF` (chars `0xFF01`–`0xFF04`) — its **only** service, and it is **not included in the advertising payload**: the adv data is flags + name only, so filter by device name, not by service UUID | `a24080b2-8857-4785-b3ba-a43b66af4f28` (128-bit, advertised) — plus `0x00FF` for OTA (chars `0xFF01`–`0xFF03`) |
| Standard SIG services | none — no HRS / Battery / DIS. (Like every BLE device, service discovery will also show the mandatory GAP `0x1800` and GATT `0x1801` — ignore them.) | HRS `0x180D`, Battery `0x180F`, DIS `0x180A` |
| Encryption / bonding | none | none |
| Negotiated MTU | requests 247 | requests 247 |
| Connection interval (typical) | 20–30 ms, slave latency 1, **32 s** supervision timeout | per-central, picked from the PEER_ROLE byte: DASHBOARD → LOW_LATENCY (15–30 ms), GLASSES → BATCHED (50–100 ms); **20 s** supervision timeout in both profiles — a dead link takes up to 20 s to surface |
| Simultaneous clients | **1** — advertising stops on connect, so a second central cannot discover or connect while another client (e.g. the Narbis app/dashboard) holds the link | up to 3 centrals |
| Idle behaviour | radio fully powers down after **2 minutes** with no client connected — magnet tap to re-arm | auto-resumes advertising on disconnect |

> ### ⚠️ Critical gotcha — UUID collision
>
> Both devices expose **service UUID `0x00FF`** but with **different characteristic sets**:
> - **Earclip** uses `0x00FF` purely for OTA: `0xFF01` Control, `0xFF02` Data, `0xFF03` Status.
> - **Edge** uses `0x00FF` for everything: `0xFF01` Control & all commands, `0xFF02` OTA Data, `0xFF03` Status & log/coherence/health notifications, `0xFF04` PPG stream.
>
> **Always disambiguate by advertised name.** Do not assume device type from `0x00FF` alone, and do not scan-filter on `0x00FF` expecting it to identify a device — it matches both, and the Edge never puts the service UUID in its advertising payload at all (adv data is flags + name only).

---

## 2. Scanning & connecting

### 2.1 Python (bleak)

There is no user-gesture requirement in Python — scan, filter by name, connect:

```python
import asyncio
import struct
from bleak import BleakScanner, BleakClient

# Edge glasses — service 0x00FF, expressed as full 128-bit UUIDs (bleak wants these forms)
EDGE_SVC    = '000000ff-0000-1000-8000-00805f9b34fb'
EDGE_CTRL   = '0000ff01-0000-1000-8000-00805f9b34fb'   # Control (all commands)
EDGE_DATA   = '0000ff02-0000-1000-8000-00805f9b34fb'   # OTA data
EDGE_STATUS = '0000ff03-0000-1000-8000-00805f9b34fb'   # Status multiplexer
EDGE_PPG    = '0000ff04-0000-1000-8000-00805f9b34fb'   # PPG stream

async def connect_edge() -> BleakClient:
    device = await BleakScanner.find_device_by_name('Narbis_Edge', timeout=15.0)
    if device is None:
        # 2-minute idle teardown: the radio may be fully powered down.
        raise RuntimeError('Edge not found — tap the magnet on the temple to wake it, then rescan')
    client = BleakClient(device)
    await client.connect()
    return client

async def connect_earclip() -> BleakClient:
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (ad.local_name or '').startswith('Narbis Earclip'),
        timeout=15.0,
    )
    if device is None:
        raise RuntimeError('Earclip not found')
    client = BleakClient(device)
    await client.connect()
    return client
```

Subscribing to a notify characteristic and writing a command:

```python
def on_ibi(_char, data: bytearray):
    ibi_ms, confidence_x100, flags = struct.unpack('<HBB', data)
    print(f'{60000 / ibi_ms:.0f} BPM')

await client.start_notify(IBI_UUID, on_ibi)
await client.write_gatt_char(EDGE_CTRL, bytes([0xA2, 80]), response=True)  # brightness 80%
```

Use `response=True` for all control writes (ordering + back-pressure); `response=False` is reserved for the `0xFF02` OTA data chunks — the only characteristic that exposes write-without-response ([§6](#6-ota--shared-between-both-devices)).

### 2.2 JavaScript (Web Bluetooth)

There is no background scan list in Web Bluetooth: `requestDevice()` opens the browser's device chooser and must be called from a **user gesture** (a click/tap — see [§7.1](#71-web-bluetooth-gotchas)). You connect one device per call, with its own filter:

```js
// Edge glasses
const EDGE_SVC = '000000ff-0000-1000-8000-00805f9b34fb';
const edge = await navigator.bluetooth.requestDevice({
  // The services clause never matches the Edge — the UUID is not in its advertising
  // payload (flags + name only). It's kept purely as a harmless OR-fallback; the
  // name filter is what actually matches.
  filters: [{ name: 'Narbis_Edge' }, { services: [EDGE_SVC] }],
  optionalServices: [EDGE_SVC],
});

// Earclip
const NARBIS_SVC = 'a24080b2-8857-4785-b3ba-a43b66af4f28';
const earclip = await navigator.bluetooth.requestDevice({
  filters: [{ services: [NARBIS_SVC] }, { namePrefix: 'Narbis Earclip' }],
  optionalServices: [NARBIS_SVC, 0x180d, 0x180f, 0x180a],
});
```

> ⚠️ **`optionalServices` is mandatory in Web Bluetooth.** Every service you later call `getPrimaryService()` on must appear in a filter **or** `optionalServices`, or the call throws `SecurityError`. This is the #1 Web-Bluetooth footgun — see [§7.1](#71-web-bluetooth-gotchas).

> ⚠️ **Windows caveat:** some Windows builds strip 16-bit service UUIDs from advertisements entirely — a `services:` filter can fail even for devices that do advertise their UUID. Filter by name ([§7.2](#72-windows--winrt--python-bleak-gotchas)).

### 2.3 Discovery

Both devices have small GATT tables. bleak discovers all services automatically on `connect()` — just use the characteristic UUIDs directly. Web Bluetooth discovers on demand: connect the GATT server, get the service, then each characteristic:

```js
const server = await device.gatt.connect();
const svc = await server.getPrimaryService(NARBIS_SVC); // must be in optionalServices (see §2.2)
const [chIbi, chSqi, chRaw, chBatt, chCfg, chCfgWrite, chMode] = await Promise.all([
  svc.getCharacteristic(NARBIS_CHR_IBI_UUID),
  svc.getCharacteristic(NARBIS_CHR_SQI_UUID),
  svc.getCharacteristic(NARBIS_CHR_RAW_PPG_UUID),
  svc.getCharacteristic(NARBIS_CHR_BATTERY_UUID),
  svc.getCharacteristic(NARBIS_CHR_CONFIG_UUID),
  svc.getCharacteristic(NARBIS_CHR_CONFIG_WRITE_UUID),
  svc.getCharacteristic(NARBIS_CHR_MODE_UUID),
]);
// Subscribe to a notify characteristic:
chIbi.addEventListener('characteristicvaluechanged', (e) => onIbi(e.target.value /* DataView */));
await chIbi.startNotifications();
```

### 2.4 MTU

Both devices request an ATT MTU of **247**.

- **Python:** `client.mtu_size` reports the negotiated value after connect (backend-dependent; on Windows/WinRT and Linux/BlueZ it reflects the real negotiation). Keep OTA chunks ≤ `mtu_size − 3`, or just use the safe fixed 244 B.
- **JS:** Web Bluetooth has **no MTU API** — `writeValue()` fragments for you. Keep writes ≤ 244 B and let the browser handle the rest.

### 2.5 Reconnection

Both devices auto-resume advertising on disconnect — reconnect with exponential backoff (1, 2, 4, 8, 16, 30 s).

> **Edge-only quirk:** the glasses tear down their BLE stack entirely after **2 minutes** with no client connected (`BLE_IDLE_TIMEOUT_MS = 120000`) — and the teardown fully powers down the radio. The user has to wake the device (magnet tap on the temple) to start advertising again. Surface this in your UX ("tap your glasses to wake them") rather than silently retrying forever. In Python, re-**scan** after a failed reconnect rather than reusing a cached `BLEDevice` ([§7.2](#72-windows--winrt--python-bleak-gotchas)).
>
> ⚠️ **Instruct users to tap only after the Edge is confirmed absent from scans** (after the retry guidance in [§7.2](#72-windows--winrt--python-bleak-gotchas)). Magnet gestures stay live while a client is connected ([§4.1.1](#411-standalone-programs--magnet-gestures)), so a tap during a transient link drop — or while another client holds the connection — cycles the running program, and a ≥ 5 s hold deep-sleeps the device.

After every reconnect:

- **CCCD subscriptions are per-connection** — re-enable notify on `0xFF03` (and any other notify characteristic you use) after each reconnect.
- **CCCD subscriptions are lost on disconnect; lens state is NOT — the lens FREEZES at its last commanded output.** The Edge's disconnect handler and idle radio teardown clear only connection/notification state: the lens keeps rendering its last commanded mode and duty across a disconnect, so a crashed app leaves the last tint in place (e.g. fully dark at duty 100) until reconnect, a magnet action, or session-expiry deep sleep (default 30 min, `0xA4`-configurable). Re-send your full lens setup on every reconnect because the device state is UNKNOWN (frozen), not reset — and before an *intentional* disconnect send `[0xA5, 0]` (static mode, 0 % = clear) or `[0xA7, 0x00]` (immediate sleep, clears the lens) so the wearer is not left dark. **fw ≥ 4.15.7: the freeze is a *default*, not a law** — write `[0xA3, 0x01]` once (persisted) and the glasses instead fail to a clear static lens on any disconnect: strobe stopped, duty 0, riding the `0xA0` smoothing glide if configured ([§4.3](#43-control-characteristic-0xff01--command-opcodes)). The failsafe fires when the *firmware* declares the link dead, which is bounded by the **32 s** supervision timeout — a crashed app can still leave the wearer dark for up to ~32 s, so the pre-disconnect clear write above remains good practice. Even with the failsafe set, re-send your full lens setup on reconnect. (`0xBA` breathe sync is the exception: it is not cleared by disconnect either but self-expires ~2 breath cycles after the last sync frame — time-based, connected or not — after which breathe falls back to the local integer-`0xB1` timing.) NVS-persisted params (`0xA2`, `0xA4`, `0xAB`, `0xAC`, `0xB1`–`0xB5`, `0xB8`, `0xB9`, `0xE0`; fw ≥ 4.15.7 adds `0xA0`, `0xA1`, `0xA3`) additionally survive reboots.
- **No application keep-alive is needed while connected** — the 2-minute teardown applies only when no client is connected; the idle deadline is cleared on connect.

---

## 3. Earclip BLE — full reference

The earclip exposes four services: one custom Narbis service with ten characteristics, and three standard SIG services (Heart Rate, Battery, Device Information). OTA is covered separately in [§6](#6-ota--shared-between-both-devices). SDK users can connect to the earclip directly from Python or JS — no glasses required.

All multi-byte fields are **little-endian on the wire**. Structs are byte-packed (no padding).

### 3.1 Custom Narbis service — `a24080b2-8857-4785-b3ba-a43b66af4f28`

The single best filter for "this is an earclip" is the presence of this 128-bit service after connect.

| Characteristic | UUID | Properties | Wire size | Notes |
|---|---|---|---|---|
| IBI | `78ef492f-66be-438d-a91e-ddfdb441b7bb` | read\* + notify | 4 B | One inter-beat interval |
| SQI | `2b614c61-bcdf-4a3f-a7e8-3b5a860c0347` | read\* + notify | 12 B | Signal-quality summary |
| RAW_PPG | `6bacca91-7017-40fa-bb91-4ebf28a65a99` | read\* + notify | 4 + 8·N B (N ≤ 29) | Sample batch |
| BATTERY | `b59d3ba1-78d1-4260-93c2-7e9e02329777` | read\* + notify | 4 B | Richer than `0x2A19` |
| CONFIG | `553abc98-6406-4e37-b9fd-34df85b2b6c1` | read + notify | **74 B** | Config + 16-bit CRC (config v4; was 50 B in v3, 58 B before that) |
| CONFIG_WRITE | `129fbe56-cbd6-4f52-957b-d80834d6abf3` | write | **74 B** | Config + 16-bit CRC |
| MODE | `71db6de8-5bff-480f-8db1-0d01c90d17d0` | write | **2 B** | Quick mode swap (legacy 3-B form still accepted, first byte ignored) |
| PEER_ROLE | `e987719a-26a6-48d4-b8e9-128994e62e6c` | write | 1 B | Central announces its role; earclip picks the conn-update profile. See [§3.1.8](#318-peer_role--e987719a-) |
| FACTORY_RESET | `c0e221b1-1633-0f9d-364a-7e47a8d9c411` | write | 4 B | **Destructive** — see warning below |
| DIAGNOSTICS | `31d99572-bf8a-4658-828e-4f7c138ca722` | read\* + notify | variable | Optional debug stream |

> \* IBI, SQI, RAW_PPG, BATTERY, and DIAGNOSTICS are registered read + notify, but a READ returns a **0-byte value** — don't poll them; subscribe. Only CONFIG returns data on read.

> ⚠️ **FACTORY_RESET is destructive — firmware-internal, do not touch in normal integrations.** Writing the exact 4 ASCII bytes `NUKE` (`0x4E 0x55 0x4B 0x45`, i.e. u32 LE `0x454B554E`) erases the earclip's entire persisted config namespace in NVS and immediately reverts the running configuration to firmware defaults (a CONFIG notification with the default snapshot follows) — the effect is immediate, not on next boot. Any other write length is rejected with ATT error `0x0D` (invalid attribute value length); a 4-byte write with the wrong magic is rejected with `0x13` (value not allowed).

#### 3.1.1 IBI — `78ef492f-…`

| Offset | Size | Field | Type | Notes |
|---|---|---|---|---|
| 0 | 2 | `ibi_ms` | u16 LE | Inter-beat interval; typical 300–2000 ms |
| 2 | 1 | `confidence_x100` | u8 | 0–100 → 0.00–1.00 |
| 3 | 1 | `flags` | u8 | bitmask (below) |

Flag bits:

```
0x01  ARTIFACT          beat is suspect
0x02  LOW_SQI           SQI below configured threshold at this beat
0x04  INTERPOLATED      filled in by validator (rare)
0x08  LOW_CONFIDENCE    confidence_x100 < 50
```

Notify cadence: **one notification per detected beat in BOTH profiles** (~1 Hz at rest) — this characteristic never batches. `ble_batch_period_ms` and the up-to-9-RR batching apply only to the standard HRS `0x2A37` characteristic ([§3.2](#32-heart-rate-service--0x180d)). In the `BATCHED` connection profile, the higher slave latency (4) and 50–100 ms interval can *delay delivery*, but beats are never coalesced.

Python:

```python
ibi_ms, confidence_x100, flags = struct.unpack('<HBB', data)
bpm = 60000 / ibi_ms if ibi_ms else 0
```

JS:

```js
function parseNarbisIBI(dv /* DataView */) {
  if (dv.byteLength < 4) throw new Error(`narbis IBI payload too short: ${dv.byteLength}`);
  return {
    ibi_ms:          dv.getUint16(0, true),
    confidence_x100: dv.getUint8(2),
    flags:           dv.getUint8(3),
  };
}
const bpm = ibi_ms > 0 ? Math.round(60000 / ibi_ms) : 0;
```

#### 3.1.2 SQI — `2b614c61-…`

| Offset | Size | Field | Type | Notes |
|---|---|---|---|---|
| 0 | 2 | `sqi_x100` | u16 LE | 0–100 → 0.00–1.00 |
| 2 | 4 | `dc_red` | u32 LE | Red-channel DC level, ADC counts |
| 6 | 4 | `dc_ir` | u32 LE | IR-channel DC level, ADC counts |
| 10 | 2 | `perfusion_idx_x1000` | u16 LE | Perfusion index × 1000 |

Useful as a "is the earclip on the ear and well-coupled?" indicator. Below `sqi_x100 < 30` you should warn the user.

Python:

```python
sqi_x100, dc_red, dc_ir, perfusion_idx_x1000 = struct.unpack('<HIIH', data)
```

JS:

```js
function parseSQI(dv /* DataView */) {
  if (dv.byteLength < 12) throw new Error(`SQI payload too short: ${dv.byteLength}`);
  return {
    sqi_x100:            dv.getUint16(0, true),
    dc_red:              dv.getUint32(2, true),
    dc_ir:               dv.getUint32(6, true),
    perfusion_idx_x1000: dv.getUint16(10, true),
  };
}
```

#### 3.1.3 RAW_PPG — `6bacca91-…`

Variable-length notification, gated on the `data_format` config field (see [§3.5](#35-the-2-axis-mode-model)). The earclip emits this only when `data_format` is `RAW_PPG (1)` or `IBI_PLUS_RAW (2)`. A batch flushes once per **29 accumulated samples** — not on a timer.

Header (4 B):

| Offset | Size | Field | Type |
|---|---|---|---|
| 0 | 2 | `sample_rate_hz` | u16 LE |
| 2 | 2 | `n_samples` | u16 LE (≤ 29) |

Then `n_samples` × 8 B:

| Offset | Size | Field | Type |
|---|---|---|---|
| +0 | 4 | `red` | u32 LE (ADC counts) |
| +4 | 4 | `ir` | u32 LE (ADC counts) |

Maximum payload: 4 + 29·8 = **236 B**. Fits inside the negotiated 247-B MTU comfortably.

Python:

```python
def parse_raw_ppg(data: bytes):
    sample_rate_hz, n_samples = struct.unpack_from('<HH', data, 0)
    if len(data) < 4 + n_samples * 8:
        raise ValueError(f'raw PPG truncated: have {len(data)}, need {4 + n_samples * 8}')
    samples = [struct.unpack_from('<II', data, 4 + i * 8) for i in range(n_samples)]  # (red, ir)
    return sample_rate_hz, samples
```

JS:

```js
function parseRawPPG(dv /* DataView */) {
  if (dv.byteLength < 4) throw new Error(`raw PPG header too short: ${dv.byteLength}`);
  const sample_rate_hz = dv.getUint16(0, true);
  const n_samples = dv.getUint16(2, true);
  const expected = 4 + n_samples * 8;
  if (dv.byteLength < expected) throw new Error(`raw PPG truncated: have ${dv.byteLength}, need ${expected}`);
  const samples = [];
  let off = 4;
  for (let i = 0; i < n_samples; i++) {
    const red = dv.getUint32(off, true); off += 4;
    const ir  = dv.getUint32(off, true); off += 4;
    samples.push({ red, ir });
  }
  return { sample_rate_hz, n_samples, samples };
}
```

#### 3.1.4 BATTERY — `b59d3ba1-…`

| Offset | Size | Field | Type | Notes |
|---|---|---|---|---|
| 0 | 2 | `mv` | u16 LE | Battery voltage in millivolts |
| 2 | 1 | `soc_pct` | u8 | 0–100 |
| 3 | 1 | `charging` | u8 | 0 = not charging, 1 = charging |

Use this rather than the standard `0x2A19` if you want the millivolts and charging-state bits.

Python: `mv, soc_pct, charging = struct.unpack('<HBB', data)`

JS:

```js
function parseNarbisBattery(dv /* DataView */) {
  if (dv.byteLength < 4) throw new Error(`narbis battery payload too short: ${dv.byteLength}`);
  return { mv: dv.getUint16(0, true), soc_pct: dv.getUint8(2), charging: dv.getUint8(3) };
}
```

#### 3.1.5 CONFIG — `553abc98-…`

Read or subscribe to this to get the full live `narbis_runtime_config_t`. Wire layout is the **72-byte** packed struct followed by a 2-byte CRC-16-CCITT-FALSE (poly `0x1021`, init `0xFFFF`, no reflect, no xor-out) for a total of **74 B** (`NARBIS_CONFIG_WIRE_SIZE`).

> **Versioning note.** The struct shrank from 56 B → 48 B in `config_version 3` when `transport_mode`, `partner_mac[6]`, and `espnow_channel` were removed (the old ESP-NOW link was deleted; the earclip is BLE-only now), then grew from 48 B → 72 B in `config_version 4` with 16 adaptive-detector + auxiliary fields appended. The first 48 bytes are identical between v3 and v4, so a client that only reads the v3 prefix still gets correct values for everything it knows about.

The struct field-by-field is in [§3.6](#36-the-runtime-config-struct).

The earclip notifies on this characteristic **after every successful CONFIG_WRITE or MODE write** so subscribed clients see fresh config without polling.

#### 3.1.6 CONFIG_WRITE — `129fbe56-…`

Write 74 B (full 72-B config + 2-B CRC) to apply settings. The firmware validates ranges, applies in place, persists to NVS, then notifies on the CONFIG characteristic.

If the CRC is bad or any field is out of range the firmware returns a BLE ATT error code on the write — in bleak this surfaces as a `BleakError` from `write_gatt_char(..., response=True)`; in Web Bluetooth the `writeValueWithResponse()` promise rejects.

End-to-end example in [§5](#5-configuring-the-earclip).

#### 3.1.7 MODE — `71db6de8-…`

Cheap 2-byte write to swap the two mode axes without touching the rest of the config.

| Offset | Size | Field | Type |
|---|---|---|---|
| 0 | 1 | `ble_profile` | `0` BATCHED, `1` LOW_LATENCY |
| 1 | 1 | `data_format` | `0` IBI_ONLY, `1` RAW_PPG, `2` IBI_PLUS_RAW |

Python:

```python
await client.write_gatt_char(MODE_UUID, bytes([1, 0]), response=True)  # LOW_LATENCY + IBI_ONLY
```

JS:

```js
async function writeMode(chMode, profile, format) {
  const buf = new Uint8Array([profile & 0xff, format & 0xff]); // [1, 0] = LOW_LATENCY + IBI_ONLY
  await chMode.writeValueWithResponse(buf);
}
```

> **Legacy compatibility.** The earclip still accepts a 3-byte write (the old `[transport_mode, ble_profile, data_format]` form) but ignores the first byte. New clients should write 2 bytes.

#### 3.1.8 PEER_ROLE — `e987719a-…`

A 1-byte write characteristic. Each connecting central writes its role on connect, and the earclip uses that single byte to pick the BLE conn-update profile for *that specific connection*.

| Value | Symbol | Profile applied |
|---|---|---|
| `0` | UNKNOWN | `BATCHED` — treated the same as GLASSES. Writing 0 is **not** a no-op: it immediately applies the BATCHED conn-update profile (50–100 ms interval, latency 4) to that connection, so a client that previously wrote 1 (LOW_LATENCY) gets demoted. Never write 0 to "keep" an existing profile. |
| `1` | DASHBOARD | `LOW_LATENCY` — 15–30 ms interval, latency 0, notify every beat. **Use this from your app.** |
| `2` | GLASSES | `BATCHED` — 50–100 ms interval, latency 4, batched notifies. The Edge uses this when it connects as a central. |

The role write is **not persisted** by the earclip — every central must re-announce on every connect. **Write it before enabling any notifications**, otherwise your first notifications arrive under the slower global-default `BATCHED` profile while the conn-update catches up.

Python:

```python
# Right after connecting, write your role *first*.
await client.write_gatt_char(PEER_ROLE_UUID, bytes([0x01]), response=True)  # DASHBOARD
```

JS:

```js
const NARBIS_PEER_ROLE_DASHBOARD = 0x01;
// Older earclip firmware lacks this characteristic, so wrap in try/catch and continue
// (they fall back to BATCHED, which is fine).
try {
  const chPeerRole = await svc.getCharacteristic(NARBIS_CHR_PEER_ROLE_UUID);
  await chPeerRole.writeValueWithResponse(new Uint8Array([NARBIS_PEER_ROLE_DASHBOARD]));
} catch (err) { /* optional on older firmware */ }
```

> Why it matters: on a multi-central earclip (your app + the Edge), you want one set of conn parameters tuned for live UI updates and a different set tuned for power-efficient relay. PEER_ROLE lets each peer get its own profile rather than fighting over a single global setting.

#### 3.1.9 DIAGNOSTICS — `31d99572-…`

Optional debug stream gated by the master `diagnostics_enabled` flag and the `diagnostics_mask` bitmask in the runtime config. Frame format:

```
[seq:u16 LE][n:u8] then n × [stream_id:u8, len:u8, payload:len B]
```

Stream IDs:

```
0x01  PRE_FILTER       raw DC-removed PPG samples
0x02  POST_FILTER      bandpass-filtered samples
0x04  PEAK_CAND        Elgendi peak candidates pre-validator
0x08  AGC_EVENT        per-AGC-step LED current changes
0x10  FIFO_OCCUP       MAX3010x FIFO occupancy at each drain
0x20  DETECTOR_STATS   v4 — adaptive-detector snapshot per accepted beat:
                       timestamp_ms (u32 LE) first, then NCC ×1000 (i16),
                       adaptive-α ×1000 (u16), Kalman x̂ ms (u16), Kalman R ms² (u16),
                       beats_learned (u32), ncc_rejects (u16), kalman_rejects (u16),
                       watchdog_resets (u16), beats_in_template (u8),
                       detector_mode (u8); all LE. Only meaningful when
                       config_version 4 and detector_mode = ADAPTIVE (1) — under
                       FIXED the stream still emits one record per accepted
                       (passthrough) beat whenever mask bit 0x20 is set, but the
                       adaptive fields (NCC, α, Kalman, counters) are
                       stale/meaningless.
```

Skip this characteristic unless you're building a tuning UI.

### 3.2 Heart Rate Service — `0x180D`

Standard SIG implementation; works with any off-the-shelf HRM library.

| Characteristic | UUID | Properties | Notes |
|---|---|---|---|
| Heart Rate Measurement | `0x2A37` | notify + read | SIG flags + BPM + R-R intervals |
| Body Sensor Location | `0x2A38` | read | Returns `0x05` (Ear) |

#### Heart Rate Measurement format

```
[flags:u8]
[bpm:u8 or u16 — depending on flag bit 0]
[energy_expended:u16]?  if flag bit 3 set
[rr_interval:u16]…      if flag bit 4 set; one or more, units = 1/1024 s
```

Flag bits:

```
bit 0    HR value format: 0 = u8, 1 = u16
bit 1–2  sensor contact state
bit 3    energy expended present
bit 4    R-R interval(s) present
```

The earclip emits BPM as `u8` and includes one or more R-R intervals. R-R intervals are in **1/1024-second units**, so convert to milliseconds with `rr_ms = rr_raw * 1000 / 1024`.

In `LOW_LATENCY` profile you see one notification per beat with one R-R interval. In `BATCHED` profile you see one notification every `ble_batch_period_ms` carrying up to 9 R-R intervals.

Python (handles the full SIG flag set, including the energy-expended skip the earclip never sets — keep it if you also target generic HRMs like a Polar strap):

```python
def parse_hrm(data: bytes):
    flags = data[0]
    off = 1
    if flags & 0x01:
        bpm, = struct.unpack_from('<H', data, off); off += 2
    else:
        bpm = data[off]; off += 1
    if flags & 0x08:
        off += 2  # skip energy expended (never set by the earclip)
    rr_ms = []
    if flags & 0x10:
        while off + 2 <= len(data):
            raw, = struct.unpack_from('<H', data, off); off += 2
            rr_ms.append(round(raw * 1000 / 1024))  # 1/1024 s → ms
    return bpm, rr_ms
```

JS:

```js
function parseHeartRateMeasurement(dv /* DataView */) {
  const flags = dv.getUint8(0);
  let off = 1;
  let bpm;
  if (flags & 0x01) { bpm = dv.getUint16(off, true); off += 2; }   // bit0: u16 vs u8
  else              { bpm = dv.getUint8(off);         off += 1; }
  const rrIntervals_ms = [];
  if (flags & 0x10) {                                              // bit4: RR present
    while (off + 2 <= dv.byteLength) {
      const raw = dv.getUint16(off, true); off += 2;
      rrIntervals_ms.push(Math.round((raw * 1000) / 1024));        // 1/1024 s → ms
    }
  }
  return { bpm, rrIntervals_ms };
}
```

> The JS deliberately omits the energy-expended skip (bit 3) — the earclip never sets that flag. If you reuse this parser for a generic HRM that might, add the Python version's `if flags & 0x08: off += 2` skip.

### 3.3 Battery Service — `0x180F`

| Characteristic | UUID | Properties | Notes |
|---|---|---|---|
| Battery Level | `0x2A19` | read + notify | Single `u8` percent (0–100) |

If you want voltage and charging state, prefer the custom Narbis BATTERY characteristic ([§3.1.4](#314-battery--b59d3ba1-)).

### 3.4 Device Information Service — `0x180A`

All read-only strings.

| Characteristic | UUID | Value |
|---|---|---|
| Manufacturer Name | `0x2A29` | `Narbis Inc.` |
| Model Number | `0x2A24` | `Earclip-001` |
| Hardware Revision | `0x2A27` | `C6_proto_rev1` |
| Firmware Revision | `0x2A26` | populated from app descriptor at boot, e.g. `1.0.3` |
| Serial Number | `0x2A25` | BLE MAC in hex, e.g. `A1B2C3D4E5F6` |

Read the firmware revision before doing OTA so you can decide whether the user is already up-to-date. (Note: the **Edge does not expose DIS** and has no version characteristic — the only version readback on the glasses is the informal `0xF1` hello line emitted on `0xFF03` subscribe, fw ≥ 4.12.1 ([§4.4.2](#442-log-string-0xf1--variable)); see [§4.6.7](#467-backward-compatibility--version).)

### 3.5 The 2-axis mode model

Two orthogonal config axes determine what the earclip emits:

```
ble_profile     BATCHED (0)   | LOW_LATENCY (1)
                Notify cadence on the standard HRS 0x2A37 characteristic
                only — the custom IBI characteristic notifies per beat in
                both profiles (§3.1.1).
                NOTE: this is the global default; PEER_ROLE overrides
                it on a per-connection basis (see §3.1.8).

data_format     IBI_ONLY (0)  | RAW_PPG (1) | IBI_PLUS_RAW (2)
                Whether RAW_PPG notifications fire.
```

Common pairings (write `[ble_profile, data_format]` to MODE):

| Use case | Mode pair |
|---|---|
| Live BPM display | `LOW_LATENCY, IBI_ONLY` |
| Background HRV recording | `BATCHED, IBI_ONLY` |
| Raw waveform view (tuning) | `BATCHED, RAW_PPG` |
| Both at once | `LOW_LATENCY, IBI_PLUS_RAW` |

> In practice you usually leave the global `ble_profile` alone and let PEER_ROLE pick the right one for your connection. The MODE write is mainly useful for swapping `data_format` on the fly.

### 3.6 The runtime config struct

The full `narbis_runtime_config_t` is **72 bytes packed** (`config_version 4`), followed by 2 bytes of CRC = **74 bytes on the wire** (`NARBIS_CONFIG_WIRE_SIZE`). Field offsets are exact (byte-packed, no padding except the explicit `reserved_agc` byte).

> **Migration note.** Three layouts have shipped:
> - `config_version 1`/`2` — legacy, **58-byte** payload with `transport_mode`, `partner_mac[6]`, `espnow_channel`.
> - `config_version 3` — **50-byte** payload (ESP-NOW fields removed).
> - `config_version 4` — current, **74-byte** payload (adaptive-detector + auxiliary fields appended).
>
> The first 48 bytes of v3 and v4 are **byte-identical**. A client that only knows v3 can still safely read everything it understands by ignoring bytes 48..71. The version byte is **firmware-owned**: on a CONFIG_WRITE the firmware ignores whatever `config_version` the writer sent and forces it to `4` before persisting (a v3-style write is accepted, not rejected — write validation only range-checks the fields). The `config_version ≥ 4` check runs at NVS **load**: a persisted v1/v2/v3 blob is rejected at boot and the firmware falls back to defaults.

| Offset | Size | Field | Default | Range / values | Notes |
|---|---|---|---|---|---|
| 0 | 2 | `config_version` | 4 | u16 LE | Firmware-owned: writer's value is ignored and forced to `4` on every accepted write |
| 2 | 2 | `sample_rate_hz` | 200 | 50, 100, 200, 400 | **Reboot to apply** |
| 4 | 2 | `led_red_ma_x10` | 70 | 0–510 (×10 mA) | Default 7.0 mA |
| 6 | 2 | `led_ir_ma_x10` | 70 | 0–510 (×10 mA) | Default 7.0 mA |
| 8 | 1 | `agc_enabled` | 1 | 0 / 1 | |
| 9 | 1 | `reserved_agc` | 0 | — | Padding; write 0 |
| 10 | 2 | `agc_update_period_ms` | 200 | u16 LE | |
| 12 | 4 | `agc_target_dc_min` | 30000 | u32 LE, ADC counts | **Not range-checked** — a zero silently breaks AGC ([§5.2](#52-python--full-config-serializer--round-trip-write)) |
| 16 | 4 | `agc_target_dc_max` | 100000 | u32 LE, ADC counts | **Not range-checked** — a zero silently breaks AGC |
| 20 | 2 | `agc_step_ma_x10` | 5 | u16 LE (×10 mA) | **Not range-checked** — a zero silently breaks AGC |
| 22 | 2 | `bandpass_low_hz_x100` | 50 | u16 LE (×100 Hz) | 0.50 Hz default |
| 24 | 2 | `bandpass_high_hz_x100` | 800 | u16 LE (×100 Hz) | 8.00 Hz default; must be > low |
| 26 | 2 | `elgendi_w1_ms` | 111 | u16 LE | Systolic peak window |
| 28 | 2 | `elgendi_w2_ms` | 667 | u16 LE | Beat window; must be > w1 |
| 30 | 2 | `elgendi_beta_x1000` | 20 | u16 LE (×1000) | Offset coefficient, default 0.020 |
| 32 | 2 | `sqi_threshold_x100` | 50 | u16 LE (×100) | Min SQI to emit IBI |
| 34 | 2 | `ibi_min_ms` | 300 | u16 LE, ≥ 1 (non-zero) | Validator floor (~200 BPM); also Elgendi refractory; must be < `ibi_max_ms` |
| 36 | 2 | `ibi_max_ms` | 2000 | u16 LE | Validator ceiling (~30 BPM) |
| 38 | 1 | `ibi_max_delta_pct` | 30 | 0–100 | Continuity threshold |
| 39 | 1 | `ble_profile` | 0 (BATCHED) | 0, 1 | Global default; PEER_ROLE overrides per-connection |
| 40 | 1 | `data_format` | 0 (IBI_ONLY) | 0, 1, 2 | See §3.5 |
| 41 | 2 | `ble_batch_period_ms` | 500 | u16 LE | BATCHED-mode flush interval |
| 43 | 1 | `diagnostics_enabled` | 1 | 0 / 1 | Master gate for DIAGNOSTICS char |
| 44 | 1 | `light_sleep_enabled` | 1 | 0 / 1 | |
| 45 | 1 | `diagnostics_mask` | 0 | bitmask | See §3.1.9 |
| 46 | 2 | `battery_low_mv` | 3300 | u16 LE | Below this → low-battery indication |
|  |  | *—— end of v3 prefix; v4 fields start here ——* |  |  |  |
| 48 | 1 | `detector_mode` | 0 (FIXED) | 0 = FIXED, 1 = ADAPTIVE | `narbis_detector_mode_t`. **FIXED makes every field below a no-op** — sticks to the proven Elgendi pipeline. Set to ADAPTIVE to enable Kalman+NCC. |
| 49 | 1 | `template_max_beats` | 10 | u8 | Rolling NCC template depth |
| 50 | 1 | `template_warmup_beats` | 4 | u8 | Beats before NCC gate activates |
| 51 | 1 | `kalman_warmup_beats` | 5 | u8 | Beats before Kalman gate activates |
| 52 | 2 | `template_window_ms` | 200 | u16 LE | Matched-filter window length, ms |
| 54 | 2 | `ncc_min_x1000` | 500 | 0–1000 (×1000) | NCC admit threshold (0.500 default) |
| 56 | 2 | `ncc_learn_min_x1000` | 750 | 0–1000 (×1000) | NCC template-learning threshold (must be ≥ `ncc_min_x1000`) |
| 58 | 2 | `kalman_q_ms2` | 400 | u16 LE | Process noise variance, ms² |
| 60 | 2 | `kalman_r_ms2` | 2500 | u16 LE | Measurement noise baseline, ms² |
| 62 | 1 | `kalman_sigma_x10` | 30 | u8 (×10) | IBI gate width, σ ×10 (3.0σ default) |
| 63 | 1 | `watchdog_max_consec_rejects` | 5 | u8 | Consecutive rejects → full detector reset |
| 64 | 2 | `watchdog_silence_ms` | 4000 | u16 LE | Silence → full detector reset, ms |
| 66 | 2 | `alpha_min_x1000` | 10 | 1–999 (×1000), non-zero | Adaptive-α floor (0.010 default) |
| 68 | 2 | `alpha_max_x1000` | 500 | ≤ 1000 (×1000) | Adaptive-α ceiling (0.500 default; must be > `alpha_min_x1000`) |
| 70 | 1 | `elgendi_loose_mode` | 0 | 0 / 1 | Relax Elgendi β and NCC admit by 50% for motion tolerance. Repurposed from the former `agc_adaptive_step` byte (same offset, same range) |
| 71 | 1 | `refractory_ibi_pct` | 60 | 0–100 | Refractory window upper bound, % of last IBI |
| **72** | 2 | **CRC16** | — | u16 LE | CRC-16-CCITT-FALSE over bytes 0..71 |

> **Removed in v3 (do not look for them):** `transport_mode` (offset 39 in the old layout), `partner_mac[6]` (offset 44), `espnow_channel` (offset 50). All ESP-NOW state is gone — the earclip↔Edge link is BLE only.

> **Validation rules the firmware enforces.** Writes are rejected if any of these fail: `sample_rate_hz` is one of {50, 100, 200, 400}; LED currents ≤ 510; `bandpass_low < bandpass_high`; `elgendi_w1 < elgendi_w2`; `ibi_min < ibi_max`; mode enums in range; `battery_low_mv` 2800–4200; `detector_mode` is `FIXED (0)` or `ADAPTIVE (1)`; `ncc_min_x1000 ≤ ncc_learn_min_x1000`; `ncc_min_x1000 ≤ 1000` and `ncc_learn_min_x1000 ≤ 1000`; `alpha_min_x1000 < alpha_max_x1000`; `alpha_max_x1000 ≤ 1000`; `template_max_beats` 1–16; `template_window_ms` 80–1000; `kalman_sigma_x10` 5–100; `watchdog_silence_ms` 500–60000; and non-zero: `kalman_q_ms2`, `kalman_r_ms2`, `watchdog_max_consec_rejects`, `bandpass_low`, `elgendi_w1`, `elgendi_w2`, `alpha_min_x1000`, `ibi_min_ms`.

Full Python and JS serializers — including the CRC-16-CCITT-FALSE implementation — are in [§5](#5-configuring-the-earclip).

---

## 4. Edge glasses BLE — full reference

The Edge advertises one custom service, **`0x00FF`**, with four characteristics (`0xFF01`–`0xFF04`). There are no standard SIG services. The device does not advertise its service UUID in the GAP payload reliably — filter by name `Narbis_Edge`.

> **No power telemetry, no version characteristic.** The Edge exposes **no Battery Service and no DIS** — glasses battery level is not readable over BLE, and there is no version characteristic to read. (The `0xF8` battery frame on `0xFF03` is the *earclip's* battery, relay builds only.) For the firmware version, subscribe to `0xFF03` and parse the `Narbis fw v…` `0xF1` log line emitted on subscribe (fw ≥ 4.12.1, [§4.4.2](#442-log-string-0xf1--variable)); only fall back to sending version-gated opcodes unconditionally with graceful fallback ([§9.3](#93-firmware-featureversion-matrix)) if that line never arrives.

### 4.1 Advertising / connection parameters

| Setting | Value |
|---|---|
| Device name | `Narbis_Edge` (exact) |
| Advertising interval | 100–200 ms |
| Adv type | connectable + scannable (`ADV_IND`) |
| TX power | **0 dBm uniform** (all TX types: ADV / SCAN / CONN) |
| Adv flags | GENERAL_DISCOVERABLE + BR/EDR_NOT_SUPPORTED |
| Idle teardown | After **2 minutes** with no client connected (`BLE_IDLE_TIMEOUT_MS = 120000`), the BLE stack shuts down completely (full radio power-down). Re-armed on magnet tap or wake. |
| Requested MTU | **247** |
| Connection interval | 20–30 ms |
| Slave latency | 1 |
| Supervision timeout | **32 s** — the BLE-spec max (`supervision_timeout = 3200`; set this high because the OTA begin erases an image-sized flash region, which can block the radio well past 19 s) |
| Client connections | **1** — advertising stops on connect, so the Edge is invisible to scans while another client holds the link |
| Pairing / encryption | none |

#### 4.1.1 Standalone programs & magnet gestures

The glasses work without any app. A short magnet tap (0.15–4 s) on the temple cycles three sensor-free programs; the lens signals the new program with N slow fade-dark pulses:

| Program | Behavior |
|---|---|
| 1 — Breathe | 6 BPM sine, lens tint follows the waveform (boot default) |
| 2 — Breathe + Strobe | 10 Hz strobe, dark-phase duty modulated by the breathing waveform |
| 3 — Strobe | Plain 10 Hz strobe |

> The behaviors above are **factory defaults**, not fixed program properties. The standalone programs render from the same NVS-persisted parameters the opcodes write — breathe rate/shape (`0xB1`/`0xB2`/`0xB5`) and strobe frequency/duty (`0xAB`/`0xAC`, defaults 10 Hz / 50 %) — so values your app persists permanently change the no-app magnet-tap programs (e.g. a persisted 12 BPM or 17 Hz strobe becomes the new Program 1/3 behavior). `[0xBF, 0x00]` (factory reset, [§4.3](#43-control-characteristic-0xff01--command-opcodes)) restores the defaults.

Hold the magnet closed **≥ 5 s** for deep sleep. (On relay-enabled builds, 5 short taps also trigger "forget earclip" — see [§4.7](#47-the-edge-as-relay).)

> ⚠️ **Magnet gestures are NOT disabled while a client is connected** — only OTA mode suspends gesture handling. A short tap (0.15–4 s) mid-session unconditionally cycles the program, overwriting your app's lens mode (e.g. a static feedback duty becomes program 2 = 10 Hz breathe+strobe), and a ≥ 5 s hold deep-sleeps the device and drops the connection. In reconnect UX, tell users to tap **only after** the device is confirmed absent from scans ([§2.5](#25-reconnection)/[§7.2](#72-windows--winrt--python-bleak-gotchas)) — a tap during a transient link drop, or while another client holds the connection, changes the running program or sleeps the device. Apps that care about lens state should watch the `led_mode` byte (offset 20) of the 1 Hz `0xF3` health frame ([§4.4.4](#444-health-telemetry-0xf3--22-b)) to detect an external takeover and re-assert their mode — noting `led_mode`/`led_duty` exist only on the 22-byte frame (late glasses fw 4.15.3+; older firmware emits the 20-byte form without them, [§9.3](#93-firmware-featureversion-matrix)).

#### 4.1.2 Session auto-sleep — the `0xA4` timer

> ### 🚨 CRITICAL — the glasses deep-sleep when the session timer expires
>
> - Default session length is **30 minutes** (`DEFAULT_SESSION_MIN`), and the `0xA4` value is **persisted in NVS** — a previous client's setting survives.
> - The session clock starts at **device wake/boot** (or sensor plug-in). **Writing `0xA4` does NOT restart it** — it only changes the total.
> - At expiry the device **enters deep sleep**: the lens goes dark, BLE drops, and a magnet tap is required to wake it.
>
> **Recipe:** have the user wake the glasses at session start, then immediately write `[0xA4, minutes]` ≥ your planned session length (max 60). Sessions longer than 60 minutes require a mid-session re-wake.
>
> **Tracking remaining time:** `uptime_s` in the 1 Hz `0xF3` health frame ([§4.4.4](#444-health-telemetry-0xf3--22-b)) counts from the same origin as the session clock (device wake/boot), so remaining session time = (`0xA4` minutes × 60) − `uptime_s`. The firmware sends **no warning before expiry** — deep sleep hits mid-session unannounced — so use this to warn or wind down your protocol in time. (On legacy builds with the on-glasses PPG sensor, a sensor plug-in restarts the session clock but not `uptime_s`, so the estimate errs on the early/safe side.)

### 4.2 Service `0x00FF` — characteristic map

| Characteristic | UUID | Properties | Direction | Purpose |
|---|---|---|---|---|
| Control | `0xFF01` | read + write | client → device | All commands; see §4.3 |
| OTA Data | `0xFF02` | write + write-no-response | client → device | OTA payload chunks; see §6 |
| Status | `0xFF03` | read + notify | device → client | Multiplexed by leading byte: ADC stats, log, coherence, health, relay, OTA status |
| PPG Stream | `0xFF04` | read + notify | device → client | Batched PPG samples + beat info — **not emitted on current fw** ([§4.5](#45-ppg-stream-characteristic-0xff04)); layout kept for reference |

Both `0xFF03` and `0xFF04` have a CCCD descriptor (`0x2902`) — enable it with `start_notify()` (bleak) / `startNotifications()` (Web Bluetooth). (`0xFF04` currently yields no notifications — [§4.5](#45-ppg-stream-characteristic-0xff04).)

> **Plain notify.** Subscribe to `0xFF03` by writing `0x0001` (notifications) to the CCCD — `start_notify()` / `startNotifications()` do this for you. A client that enables *indications* (`0x0002`) instead receives **nothing**. Notifications are unacknowledged, so frames can arrive back-to-back.

### 4.3 Control characteristic `0xFF01` — command opcodes

> ### 🚨 CRITICAL — every command write must be at least 2 bytes
>
> Any **1-byte write is interpreted as the legacy opacity command**: the byte is a direct lens duty, `0–255` → `0–100 %`, and it **stops whatever mode is running**. A bare `[0xA6]` or `[0xA7]` is treated as an opacity write of 166 or 167 — not a command. **Pad argument-less opcodes with `0x00`**: `[0xA6, 0x00]`, `[0xA7, 0x00]`.
>
> The legacy opacity write is also a feature: it's the cheapest way to set a static tint, streamable for continuous feedback under the same cadence rules as [§4.6.1](#461-continuous-opacity-feedback--the-biofeedback-pattern) — **~10–12 Hz recommended (12 Hz is the production-proven rate); the link tolerates up to ~20 Hz only as a ceiling**. `0x00` = fully clear, `0xFF` = fully dark. Not persisted.

> ### 🚨 CRITICAL — serialize all writes to `0xFF01`
>
> Issue the next write only after the previous one completes. Concurrent GATT writes to the same characteristic fail on WinRT ("operation already in progress") and Web Bluetooth alike ([§7](#7-client-gotchas)). Route commands **and** the continuous duty stream ([§4.6.1](#461-continuous-opacity-feedback--the-biofeedback-pattern)) through one queue.

Most commands are a 2-byte write `[opcode, arg]`. The firmware **never sends a GATT error for a bad argument** — most opcodes clamp out-of-range values into range, but a few do not: `0xB7` and `0xB8` **ignore** out-of-range arguments entirely (emitting a `0xF1` OOR log frame), and `0xE0` **rejects the whole write** if validation fails. Validate client-side, and use a write timeout if you need to detect failure.

| Opcode | Name | Arg | Persisted? | Notes |
|---|---|---|---|---|
| *(1 byte)* | Legacy opacity | 0–255 → 0–100 % static duty | no | Stops the current mode; see warning above |
| `0xA0` | Lens smoothing | 0–255 (EMA τ, ×10 ms → 0–2.55 s; 0 = off) | yes (NVS) | fw ≥ 4.15.7, older fw ignores ([§9.3](#93-firmware-featureversion-matrix)). On-device glide between **commanded static** targets (`0xA5`, the 1-byte opacity write, the `0xA3` fail-clear) — a low-rate or lossy feedback stream ([§4.6.1](#461-continuous-opacity-feedback--the-biofeedback-pattern)) renders as a glide instead of steps. Set τ ≈ 1–2× your write period (12 Hz stream → ~80–160 ms → arg 8–16). Strobe, breathe, and the standalone programs are unaffected. |
| `0xA1` | Lens max transition rate | 0–100 (%/100 ms; 0 = unlimited) | yes (NVS) | fw ≥ 4.15.7, older fw ignores. Hard slew cap on commanded static transitions, applied **after** `0xA0` smoothing — a safety envelope guaranteeing the lens can't snap even if a host streams garbage (40 ≈ full-scale in 250 ms, the breathe engine's own internal limit). Does not affect breathe/strobe waveforms. |
| `0xA2` | Set brightness | 0–100 (%) | yes (NVS) | Sets + persists the same `brightness` variable `0xA5` writes ([§4.6.1](#461-continuous-opacity-feedback--the-biofeedback-pattern)); doubles as breathe **depth** ([§4.6.3](#463-the-breathe-op-set)) |
| `0xA3` | On-disconnect behavior | `0x00` continue (default) / `0x01` fail clear | yes (NVS) | fw ≥ 4.15.7, older fw ignores. `0x01`: on link loss the glasses stop any strobe and drop to a clear static lens (duty 0, riding the `0xA0` glide if set) instead of freezing at the last output ([§2.5](#25-reconnection)). Fires when the firmware declares the link dead — bounded by the 32 s supervision timeout. Magnet-tap standalone programs still work afterwards. |
| `0xA4` | Set session duration | 1–60 (minutes) | yes | Auto-sleep (deep sleep) at session end. Default 30 min; the clock runs from device wake — writing `0xA4` changes the total but does **not** restart it ([§4.1.2](#412-session-auto-sleep--the-0xa4-timer)) |
| `0xA5` | Static LED mode | 0–100 (duty %) | no | The continuous-feedback stream opcode ([§4.6.1](#461-continuous-opacity-feedback--the-biofeedback-pattern)); writes the same variable as `0xA2`, without persisting |
| `0xA6` | Strobe LED mode | any | no | starts strobe ISR |
| `0xA7` | Sleep now | any | no | enters deep sleep |
| `0xA8` | OTA START | `[size:u32 LE]` or `0x00` | no | with the image size: erases only an image-sized region (fast); `0x00` = legacy full-slot erase — see §6 |
| `0xA9` | OTA FINISH | `0x00` | no | see §6 |
| `0xAA` | OTA CANCEL | `0x00` | no | see §6 |
| `0xAB` | Strobe frequency | 1–50 (Hz) — 2-byte form; a 3-byte deci-Hz form exists ([§4.6.6](#466-strobe)) | yes | |
| `0xAC` | Strobe duty | 10–90 (%) | yes | dark fraction of each strobe period |
| `0xAD` | OTA Page Confirm | `0x01` commit / `0x00` resend | no | see §6 |
| `0xB0` | Breathe LED mode | `0x00` breathe / `0x01` breathe+strobe | no | `0x00` (or any arg other than `0x01`) = plain breathe; `0x01` = breathe+strobe, phase-locked to `0xBA`/`0xB1`/`0xB2` (fw ≥ 4.15.6). Toggling the arg preserves breathe phase. ⚠️ A bare 1-byte `[0xB0]` is **not** breathe — it is the legacy opacity write (69 % static tint, stops the current mode); always send `[0xB0, 0x00]` (see the ≥ 2-byte warning above). |
| `0xB1` | Breathe BPM | 1–30 | yes | integer BPM — use `0xBA` for fractional rates |
| `0xB2` | Breathe inhale ratio | 10–90 (%) | yes | |
| `0xB3` | Breathe hold-top | 0–50 (×100 ms) | yes | |
| `0xB4` | Breathe hold-bottom | 0–50 (×100 ms) | yes | |
| `0xB5` | Breathe waveform | 0 sine, 1 linear | yes | |
| `0xB6` | Pulse-on-beat mode | any | no | Legacy pipeline ([§4.8](#48-legacy-on-board-coherence-pipeline-unused)). Lens pulses once per beat reaching the firmware — via the relay (relay-enabled builds only) or `0xCA` injection. |
| `0xB7` | PPG program | 0–3 | no | Legacy pipeline ([§4.8](#48-legacy-on-board-coherence-pipeline-unused)): 0 heartbeat, 1 coh-breathe, 2 coh-lens, 3 coh-breathe-strobe |
| `0xB8` | Coherence difficulty | 0–3 | yes | Legacy pipeline: easy / medium / hard / expert |
| `0xB9` | Adaptive pacer | 0/1 | yes | Legacy pipeline |
| `0xBA` | **Breathe sync** | 3 B payload | no | App-side lens phase-lock (fw ≥ 4.15.5). 4 B on the wire: `[0xBA][cycle_ms:u16 LE][inhale_pct:u8]`. Valid `cycle_ms` 2000–30000 ms (2–30 s per breath) and `inhale_pct` 10–90; out-of-range values are silently clamped (no NACK), so a cycle above 30 s renders at 30 s and will drift against your app clock. Restarts the `LED_MODE_BREATHE` cosine at the moment of the write (= your app's inhale boundary) and renders at the exact cycle length sent, so the glasses lens, an on-screen breathing cue, and an audio chime can share one clock. **Send ONLY at the breath-cycle boundary, never mid-breath** — full rationale in [§4.6.5](#465-phase-sync--the-one-rule-write-only-at-the-breath-boundary). Send it at each boundary and on connect/session start. A firmware lens slew-rate limiter fades any re-anchor (~250 ms) so resyncs never snap. Auto-expires 2 cycles after the last sync (reverts to the integer-BPM `0xB1` rate). Ignored by firmware < 4.15.5 (unknown opcode), so it's safe to always send. |
| `0xBF` | Factory reset | any | n/a | wipes the persisted-settings NVS namespace |
| `0xC0` | *(reserved)* | — | — | Listed in the firmware's internal opcode comment table but has no dispatcher case — do not use |
| `0xC1` | Forget earclip | any (ignored) | no | Relay control ([§4.7](#47-the-edge-as-relay)) — inert on stock builds. Wipes the stored earclip pairing, drops the central connection, starts a fresh scan. Visual feedback: 3 fast lens pulses. Same effect as 5 short magnet taps. |
| `0xC3` | Relay config write | 50 B payload | no | Relay control — inert on stock builds. Bytes after the opcode are forwarded as a CONFIG_WRITE to the paired earclip — but the glasses firmware ships the **v3-era `NARBIS_CONFIG_WIRE_SIZE = 50`** and forwards exactly 50 B. ⚠️ **Known version mismatch:** current earclip firmware expects the 74-B config v4 payload ([§3.6](#36-the-runtime-config-struct)), so `0xC3` must not be used until the glasses relay is rebuilt against config v4 — write earclip config via a direct earclip connection instead ([§5](#5-configuring-the-earclip)). The earclip replies via CONFIG notify, which the Edge re-emits as a `0xF4` frame. |
| `0xC4` | Toggle raw-PPG relay | `0` disable / non-zero enable | no | Relay control — inert on stock builds. Subscribes/unsubscribes the Edge's central from the earclip's RAW_PPG characteristic; while enabled, samples are forwarded as `0xF5` frames. Default: enabled at boot. |
| `0xC5` | Refresh earclip config | any (ignored) | no | Relay control — inert on stock builds. Triggers a one-shot CONFIG read on the Edge's central; the result is re-emitted as a `0xF4` frame. Send it ~2 s after the relay reports UP if no `0xF4` arrived, and expose it as a manual "reload from earclip" action. |
| `0xCA` | External-IBI injection | 4 B payload | no | Legacy pipeline ([§4.8](#48-legacy-on-board-coherence-pipeline-unused)). 5 B total on the wire: `[0xCA][ibi_ms:u16 LE][confidence:u8 0–100][flags:u8]`. Forwards an external HR source's beats into the **firmware's** coherence pipeline. Apps that compute their own feedback do **not** send this. Beats with `confidence < conf_threshold` or `flags & ARTIFACT (0x01)` are silently dropped. |
| `0xCB` | Set HR source | 0 = earclip / 1 = external | no | Legacy pipeline. `0` resumes the Edge's central scan for the earclip; `1` pauses it so the glasses don't pull earclip beats while the app is the HR authority. Not persisted — re-assert on every connect. (Moot on stock builds — the central is compile-disabled.) |
| `0xD0` | Manual detector reset | any | no | Legacy pipeline: clears beat-detection state |
| `0xE0` | Coherence pipeline tuning | 12 B payload | yes (NVS) | Legacy pipeline ([§4.8](#48-legacy-on-board-coherence-pipeline-unused)). 13 B on the wire: `[0xE0]` + packed `narbis_coh_params_t`. Validation rejects out-of-grid bins and inverted lo/hi pairs; new params apply on the next coherence compute (≤ 1 s). |

Python — one helper for every opcode (pads to the 2-byte minimum):

```python
async def send_cmd(client, opcode: int, *args: int):
    payload = bytes([opcode, *args]) if args else bytes([opcode, 0x00])  # never write 1 byte
    await client.write_gatt_char(EDGE_CTRL, payload, response=True)

await send_cmd(client, 0xA2, 80)   # brightness 80%
await send_cmd(client, 0xB0)      # enter breathe → [0xB0, 0x00]
await send_cmd(client, 0xB1, 6)   # breathe 6 BPM
```

JS — same idea (the production version chains writes onto a serial promise queue so concurrent writes never collide):

```js
async function sendCtrlCommand(chCtrl, opcode, payload /* Uint8Array | undefined */) {
  const total = Math.max(2, 1 + (payload?.length ?? 0)); // firmware treats 1-B writes as opacity → pad to 2
  const buf = new Uint8Array(total);
  buf[0] = opcode & 0xff;
  if (payload?.length) buf.set(payload, 1);
  await chCtrl.writeValueWithResponse(buf);
}
// examples:
sendCtrlCommand(chCtrl, 0xA2, new Uint8Array([Math.min(pct, 100)]));             // brightness
sendCtrlCommand(chCtrl, 0xB0);                                                   // enter breathe → [0xB0,0x00]
sendCtrlCommand(chCtrl, 0xB1, new Uint8Array([Math.max(1, Math.min(30, bpm))])); // breathe BPM
```

### 4.4 Status characteristic `0xFF03` — notification multiplexer

The Edge multiplexes several packet types onto the same characteristic, distinguished by the **first byte**.

| Type byte | Cadence | Length | Purpose |
|---|---|---|---|
| `0xF0` | *(none — not emitted on current fw, see §4.4.1)* | 11 B | Raw ADC stats (min / max / mean of last window) |
| `0xF1` | on demand | 1 + N B (N ≤ 63) | Firmware log strings (printf output) |
| `0xF2` | every 1000 ms once computing (silent until first compute — see §4.4.3) | 18 B | Coherence packet (legacy pipeline — HRV bands + score) |
| `0xF3` | every 1000 ms | 22 B | Health telemetry (uptime, heap, jitter, errors, LED state) — see §4.4.4 |
| `0xF4` | event-driven | 1 + 74 B | Relayed earclip CONFIG payload — see §4.4.5 |
| `0xF5` | event-driven | 1 + variable | Relayed earclip RAW_PPG batch — see §4.4.6 |
| `0xF6` | on connect / disconnect / 30 s | 2 B | Earclip relay link state — see §4.4.7 |
| `0xF7` | event-driven | 1 + variable | Relayed earclip diagnostics — see §4.4.8 |
| `0xF8` | event-driven | 5 B | Relayed earclip battery (binary) — see §4.4.9 |
| `0xF9` | event-driven (~1 Hz per beat) | 5 B | Relayed earclip IBI (binary) — see §4.4.10 |
| `0xFA` | every 1000 ms | 7 B | Link-quality telemetry (RSSI, MTU, drops) — see §4.4.11 |
| `0x01`–`0x08` | event-driven | 1–7 B | OTA status — see §6 |

Always subscribe to `0xFF03` **before** sending any OTA opcode, otherwise you'll miss the READY / PAGE_CRC / ERROR responses you need to drive the protocol. The relay frames `0xF4`–`0xFA` are delivered on this same characteristic, so a single subscription covers everything. (The relay frames only fire on relay-enabled builds — [§4.7](#47-the-edge-as-relay).)

Python dispatch skeleton:

```python
def on_status(_char, data: bytearray):
    t = data[0]
    if t == 0xF9 and len(data) >= 5:                      # relayed earclip IBI
        ibi_ms, conf, flags = struct.unpack_from('<HBB', data, 1)
    elif t == 0xFA and len(data) >= 7:                    # link quality
        ec_rssi, dash_rssi = struct.unpack_from('<bb', data, 1)
        mtu, drops = struct.unpack_from('<HH', data, 3)
    elif t == 0xF3 and len(data) >= 22:                   # health telemetry (22-byte form; late 4.15.3+ — the length check is the right gate)
        uptime_s, heap_free, heap_min = struct.unpack_from('<III', data, 1)
        led_mode, led_duty = data[20], data[21]           # led_duty is a 0–100 percentage
    elif t <= 0x08:                                       # OTA status frame
        ota_queue.put_nowait(bytes(data))

await client.start_notify(EDGE_STATUS, on_status)
```

#### 4.4.1 ADC stats (`0xF0`) — 11 B

> **Not emitted on current firmware (4.15.6+)** — the on-glasses PPG front-end is removed, and with it the ADC read that fed this frame; subscribing yields silence. Layout kept for reference (it may return on future hardware). On firmware that emitted it, the cadence was every 500 ms.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0xF0` | Type byte |
| 1 | 2 | `min` | u16 LE, ADC counts (0–4095) |
| 3 | 2 | `max` | u16 LE |
| 5 | 2 | `mean` | u16 LE |
| 7 | 1 | `count` | Samples in window (≤ 25) |
| 8 | 3 | reserved | 0 |

If `min == 0` or `max ≈ 4095`, the PPG sensor is disconnected or saturated. If `(max − min) < 200`, no useful signal.

#### 4.4.2 Log string (`0xF1`) — variable

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `0xF1` |
| 1..N | up to 63 | ASCII string (NUL-terminated or truncated) — string content up to 62 chars (the 63rd payload byte is the NUL terminator when the line is truncated); max notification 64 B |

Useful for debugging. The firmware emits a hello on subscribe, an "alive" heartbeat every ~30 s, and ad-hoc events (heartbeat and ad-hoc lines share the same `0xF1` type).

> **The subscribe hello doubles as the version probe.** On every `0xFF03` subscribe (rising edge), firmware ≥ 4.12.1 emits a `0xF1` log line of the form `Narbis fw v<version> test=<0|1> mode=<led_mode>` (e.g. `Narbis fw v4.15.6-strobe-sync test=0 mode=1`). The version string may carry a build suffix after the dotted numerics, so extract the leading `X.Y.Z` (e.g. regex `/Narbis fw v(\d+\.\d+\.\d+)/`). This is the only firmware-version readback on the Edge — see [§9.3](#93-firmware-featureversion-matrix).

#### 4.4.3 Coherence packet (`0xF2`) — 18 B

Emitted by the **legacy on-board coherence pipeline** ([§4.8](#48-legacy-on-board-coherence-pipeline-unused)). On stock builds it carries data only if you feed beats via `0xCA`; on relay-enabled builds it also works from earclip beats.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0xF2` | |
| 1 | 1 | `coherence` | 0–100, % |
| 2 | 2 | `resp_peak_mhz` | u16 LE, milliherz |
| 4 | 2 | `vlf_power` | u16 LE |
| 6 | 2 | `lf_power` | u16 LE |
| 8 | 2 | `hf_power` | u16 LE |
| 10 | 2 | `total_power` | u16 LE |
| 12 | 1 | `lf_norm_pct` | 0–100 |
| 13 | 1 | `hf_norm_pct` | 0–100 |
| 14 | 2 | `lf_hf_ratio_fp8_8` | u16 LE; divide by 256 for the decimal ratio |
| 16 | 1 | `n_ibis_used` | 0–120 nominal; emitted frames always carry ≥ `min_ibis` (never 0 — see below) |
| 17 | 1 | `pacer_rate_q5` | Current pacer rate in quintets (BPM × 5, 0.2-BPM resolution); divide by 5 for BPM (e.g. 30 = 6.0 BPM). Range 15–50 (3.0–10.0 BPM) while a coherence-breathe program (PPG program 1/3) is running. When the adaptive pacer is disabled (`0xB9` = 0) this reads 30, not 0 — the firmware forces it back to 30 (6.0 BPM) at every cycle boundary. 0 only before the coherence-breathe family has ever been entered since boot; after the program exits, the byte retains its last value (stale) rather than resetting to 0. |

The Edge emits **no `0xF2` frames at all until the first successful coherence compute**, which requires at least `min_ibis` beats in the window (`0xE0`-clamped 5–120, firmware default 20). An emitted frame therefore always has `n_ibis_used ≥ min_ibis` — a frame with `n_ibis_used == 0` never occurs. If the pipeline has no beats, the symptom is `0xF2` silence — see the troubleshooting matrix ([§8](#8-troubleshooting-matrix)).

#### 4.4.4 Health telemetry (`0xF3`) — 22 B

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0xF3` | |
| 1 | 4 | `uptime_s` | u32 LE; counts from device wake — usable to compute remaining session time ([§4.1.2](#412-session-auto-sleep--the-0xa4-timer)) |
| 5 | 4 | `heap_free` | u32 LE |
| 9 | 4 | `heap_min` | u32 LE; minimum free heap since boot — leak detector |
| 13 | 2 | `ppg_stack_hwm_words` | u16 LE; `0xFFFF` = >65535 |
| 15 | 2 | `ble_send_errors` | u16 LE; saturates at `0xFFFF` |
| 17 | 2 | `jitter_max_us` | u16 LE; cumulative since boot on current firmware (the 5-s window reset was removed with the on-glasses PPG pipeline — only older PPG-enabled firmware reset it every 5 s); saturates at `0xFFFF` |
| 19 | 1 | `jitter_ticks_over` | u8; cumulative since boot on current firmware (same history — 5-s reset only on older PPG-enabled firmware); saturates at `0xFF` |
| 20 | 1 | `led_mode` | u8 — `led_mode_t` enum: `0` strobe, `1` static, `2` breathe, `3` breathe+strobe, `4` pulse-on-beat, `5` coherence-breathe, `6` coherence-breathe+strobe, `7` coherence-lens. Mirror of the lens-driver state machine. (There is no "off" value — a clear lens is `static`/`breathe` at duty 0.) |
| 21 | 1 | `led_duty` | u8 — effective lens duty as a **percentage, 0–100**. Snapshot of the actual lens output at emit time, not the requested duty. Useful for "is the lens doing what I asked?" overlays. |

A rising `ble_send_errors` means the device's own notifications are failing to send (congested or degraded link, notifications outpacing what the connection can carry) — it counts failed device→client NOTIFY sends, not client writes; check link quality and connection parameters rather than your write rate. Note the jitter counters are **not windowed stats** on current builds (cumulative since boot) — a "current window" jitter gauge cannot be built from them. `led_mode` + `led_duty` were added in late glasses fw 4.15.3 builds (the 4.15.3 version string covers both 20- and 22-byte builds); older firmware emits a 20-byte frame without these two bytes — key on frame length (20 vs 22 B), not on version.

#### 4.4.5 Relayed earclip CONFIG (`0xF4`) — 75 B

Relay-enabled builds only. Forwarded verbatim from the earclip's CONFIG characteristic when its value changes (or in response to a `0xC3` write you sent).

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0xF4` | Type byte |
| 1 | 72 | `narbis_runtime_config_t` | Earclip config struct ([§3.6](#36-the-runtime-config-struct)) — v4 layout |
| 73 | 2 | `crc16` | CRC-16-CCITT-FALSE over bytes 1..72 |

The 74-byte tail is **identical** to what you'd read directly from the earclip's CONFIG characteristic — same struct layout, same CRC. Reuse your earclip CONFIG parser. `0xF4` is a **length-agnostic passthrough** — the Edge forwards whatever length the earclip notifies (so a v3 earclip behind a current Edge produces a 51-byte frame with the 48-byte struct — branch on the `config_version` field at the start of the payload). Note this is unlike the *write* direction: `0xC3` is pinned to the v3-era 50-B size — see the known version mismatch in the [§4.3 `0xC3` row](#43-control-characteristic-0xff01--command-opcodes).

#### 4.4.6 Relayed earclip RAW_PPG (`0xF5`) — variable

Relay-enabled builds only. Forwarded from the earclip's RAW_PPG characteristic. Only emitted when raw-PPG relay is enabled (opcode `0xC4`; default enabled at boot).

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0xF5` | Type byte |
| 1 | 2 | `sample_rate_hz` | u16 LE |
| 3 | 2 | `n_samples` | u16 LE, ≤ 29 |
| 5 | 8·N | samples | Each `[red:u32 LE, ir:u32 LE]` |

Maximum payload: 1 + 4 + 29·8 = **237 B**. The 1-byte type prefix is the only difference vs. the earclip's direct RAW_PPG notification ([§3.1.3](#313-raw_ppg--6bacca91-)).

#### 4.4.7 Relay link state (`0xF6`) — 2 B

Tells you whether the Edge's central role currently has a healthy connection to the earclip.

> **Emitted on ALL builds — including stock.** Stock builds (relay compiled out) still emit a `0xF6` heartbeat with `linked=0` every ~30 s, plus a `0xF1` "alive" log line. Receiving `0xF6` does **not** mean a relay/earclip is present — check the `linked` byte.

On relay-enabled builds it is emitted:
- once when a client connects to the Edge (so you know the current state immediately),
- on every earclip connect / disconnect transition,
- every 30 s as a heartbeat.

| Offset | Size | Field | Values |
|---|---|---|---|
| 0 | 1 | `0xF6` | Type byte |
| 1 | 1 | `linked` | `0` = relay lost (Edge is searching for / has lost the earclip), `1` = relay linked (earclip data is flowing) |

```js
const connected = bytes[1] !== 0;   // 1 = earclip relay linked, 0 = lost
ui.setEarclipBadge(connected);
```

#### 4.4.8 Relayed earclip diagnostics (`0xF7`) — variable

Relay-enabled builds only. Forwarded earclip DIAGNOSTICS frames (see [§3.1.9](#319-diagnostics--31d99572-)). Only fires when diagnostic streams are enabled in the earclip config — usually a no-op.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `0xF7` |
| 1..N | variable | The full earclip diagnostic frame (`[seq:u16, n:u8] then n × [stream_id:u8, len:u8, payload]`) |

#### 4.4.9 Relayed earclip BATTERY (`0xF8`) — 5 B

Relay-enabled builds only. Structured battery snapshot. Mirrors the earclip's BATTERY payload ([§3.1.4](#314-battery--b59d3ba1-)) with a 1-byte type prefix. Emitted whenever the earclip's BATTERY characteristic notifies — typically every 30 s.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0xF8` | Type byte |
| 1 | 2 | `mv` | u16 LE — battery voltage, millivolts |
| 3 | 1 | `soc_pct` | u8 — state of charge, 0–100 |
| 4 | 1 | `charging` | u8 — 0 = discharging, 1 = charging |

Prefer this over parsing the human-readable `0xF1` log line (`"earclip batt soc=… mv=…"`) — same data, ~6× less air-time, no regex.

#### 4.4.10 Relayed earclip IBI (`0xF9`) — 5 B

Relay-enabled builds only (since glasses fw 4.15.2). One inter-beat-interval observation, forwarded every time the earclip's IBI characteristic notifies (~1 Hz at resting HR). Mirrors the earclip IBI payload ([§3.1.1](#311-ibi--78ef492f-)) with a 1-byte type prefix.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0xF9` | Type byte |
| 1 | 2 | `ibi_ms` | u16 LE — inter-beat interval, milliseconds (300–2000 typ.) |
| 3 | 1 | `confidence_x100` | u8 — 0–100 → 0.00–1.00 confidence |
| 4 | 1 | `flags` | u8 — beat-flag bitmask (bit 0 = artifact, see §3.1.1) |

```js
const ibi_ms = bytes[1] | (bytes[2] << 8);
const confidence_x100 = bytes[3];
const flags = bytes[4];
if (ibi_ms > 0) engine.pushBeat({ ibi_ms, confidence_x100, flags }); // gate on flags/conf client-side
```

Fires unconditionally per detected beat — apply the confidence gate / artifact filter client-side as needed.

#### 4.4.11 Link quality (`0xFA`) — 7 B

Since fw 4.15.3. 1 Hz BLE link-quality snapshot for both hops the Edge participates in. Web Bluetooth doesn't expose RSSI to JS, so the Edge measures it device-side and ships it up. Use it to drive a "signal strength" UI element.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0xFA` | Type byte |
| 1 | 1 | `earclip_rssi` | i8 — glasses ↔ earclip link RSSI, dBm. `0x7F` (= 127) sentinel = no link |
| 2 | 1 | `dashboard_rssi` | i8 — glasses ↔ client link RSSI, dBm. `0x7F` sentinel = no link |
| 3 | 2 | `mtu` | u16 LE — current ATT MTU on the client link (0 if no link) |
| 5 | 2 | `drops` | u16 LE — clamped `ble_send_errors`, cumulative notify failures (saturates at `0xFFFF`) |

The client-side RSSI on this frame is the Edge's view of you — it should always be present when you're receiving the frame. The earclip-side RSSI is `0x7F` whenever the relay link is down (matches `0xF6 linked=0`).

```js
const earclipRssi   = dv.getInt8(1);          // 0x7F (127) = no link
const dashboardRssi = dv.getInt8(2);
const mtu   = bytes[3] | (bytes[4] << 8);
const drops = bytes[5] | (bytes[6] << 8);
ui.setBars(earclipRssi === 0x7F ? null : earclipRssi,
           dashboardRssi === 0x7F ? null : dashboardRssi);
```

### 4.5 PPG stream characteristic `0xFF04`

> **Not emitted on current firmware (4.15.6+)** — the on-glasses PPG front-end is removed; subscribing yields silence. Layout kept for reference (it may return on future hardware).

Batched samples with embedded beat detection, from the Edge's on-board PPG front-end (removed on current hardware — see note above). The last emitting firmware shipped **format `0x03`** (since fw 4.14.9). An older format `0x02` exists in legacy firmware and is documented at the end for back-compat.

#### Format `0x03` — header (6 B)

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `0x03` | Type byte |
| 1 | 1 | `n_samples` | typically 10, ≤ 10 |
| 2 | 4 | `base_timestamp_ms` | u32 LE; time of `samples[0]` from the device timer |

#### Per-sample (8 B each, `n_samples` times)

| Offset | Size | Field | Notes |
|---|---|---|---|
| +0 | 2 | `raw_adc` | u16 LE, 12-bit value (0–4095) |
| +2 | 2 | `sample_index` | u16 LE; absolute, wraps at 65536 (≈21.8 min @ 50 Hz) |
| +4 | 1 | `flags` | bit 0 = beat detected, bit 1 = in-block state |
| +5 | 2 | `ibi_ms` | u16 LE; interval since last beat, 0 if none yet |
| +7 | 1 | `bpm` | u8; current BPM estimate, 0 if < 2 beats |

Total notification size with N=10: 6 + 80 = 86 B. On firmware that emitted this stream, the cadence was ≈ 200 ms (every 10 samples at 50 Hz).

Reconstruct per-sample timestamps client-side: `ts[i] = base_timestamp_ms + i * 20` (50 Hz nominal, no per-sample jitter field).

Python:

```python
def parse_edge_ppg(data: bytes):
    if len(data) < 6 or data[0] != 0x03:
        return None
    n = data[1]
    if len(data) != 6 + n * 8:
        return None
    base_timestamp_ms, = struct.unpack_from('<I', data, 2)
    samples = [struct.unpack_from('<HHBHB', data, 6 + i * 8) for i in range(n)]
    # each: (raw_adc, sample_index, flags, ibi_ms, bpm); beat = flags & 0x01
    return base_timestamp_ms, samples
```

JS:

```js
function parseEdgePPG(dv /* DataView */) {
  if (dv.byteLength < 6 || dv.getUint8(0) !== 0x03) return null;
  const n = dv.getUint8(1);
  if (dv.byteLength !== 6 + n * 8) return null;
  const baseTimestampMs = dv.getUint32(2, true);
  const samples = [];
  for (let i = 0; i < n; i++) {
    const o = 6 + i * 8;
    samples.push({
      raw: dv.getUint16(o, true), index: dv.getUint16(o + 2, true),
      flags: dv.getUint8(o + 4), beatDetected: (dv.getUint8(o + 4) & 1) !== 0,
      ibi_ms: dv.getUint16(o + 5, true), bpm: dv.getUint8(o + 7),
    });
  }
  return { baseTimestampMs, samples }; // ts[i] = baseTimestampMs + i*20 (50 Hz nominal)
}
```

#### Legacy format `0x02` (older firmware only)

13 B per sample, no batching:

```
[0x02][raw:u16 LE][idx:u16 LE][ts:u32 LE][flags:u8][ibi:u16 LE][bpm:u8]
```

Detect by reading the type byte; both share characteristic `0xFF04`.

### 4.6 Driving the Edge lens

Your app owns the feedback algorithm and tells the lens what to do. This section is the complete drive surface: continuous-opacity feedback, how to make the lens breathe, how to strobe, the opacity curve, and — the part that bites everyone — how to keep the lens **in phase** with your on-screen cue and audio.

#### 4.6.1 Continuous-opacity feedback — the biofeedback pattern

The primary third-party integration pattern: your software produces a feedback value and the lens tracks it continuously — a **wearable screen dimmer**. Anywhere a protocol would dim/brighten the training display (out of condition → dim, in condition → clear), map that same value to lens duty instead; threshold-style binary reward and proportional analog mappings both reduce to this one write loop (worked example: `python-SDK/examples/screen_dimmer.py`).

- **Primary method:** stream `[0xA5, duty]` (duty 0–100) at **~10–12 Hz** — the production dashboard uses 12 Hz.
- **Decimate** your source signal (e.g. a 256 Hz EEG index) down to that rate; map 0..1 → 0..100.
- Three hardening rules (production-proven):
  1. **Coalesce** — skip the write if the duty is unchanged.
  2. **Never overlap writes** — if one is in flight, drop the frame (the next catches up). See the serialization rule in [§4.3](#43-control-characteristic-0xff01--command-opcodes).
  3. **Keep exactly one write in flight** — `0xFF01` is **write-with-response only** on current firmware ([§4.2](#42-service-0x00ff--characteristic-map); only OTA `0xFF02` exposes write-no-response), and the with-response round-trip is exactly why the one-write-in-flight rule matters at 12 Hz. If you add a property guard like the production dashboard's (`ch.properties?.writeWithoutResponse` → without-response, else with-response), write-without-response will be used automatically if a future firmware adds the property.
- The 1-byte legacy opacity write (0–255, [§4.3](#43-control-characteristic-0xff01--command-opcodes)) is an equivalent alternative under the same cadence rules.
- **Smoothing the stream (fw ≥ 4.15.7):** write `[0xA0, τ]` once (persisted) and the device glides between your streamed targets with an EMA of time constant τ×10 ms — steps from a 10–12 Hz stream, RF retransmit gaps, and rate drops all render as smooth motion. Rule of thumb: τ ≈ 1–2× your write period (12 Hz → arg 8–16 ≈ 80–160 ms). Pair with `[0xA1, slew]` if you also want a hard cap on how fast the lens may move ([§4.3](#43-control-characteristic-0xff01--command-opcodes)). Both are ignored by older firmware, so send them unconditionally.

Language-neutral wire sequence (worked examples in the [quickstart](#quickstart--make-the-lens-respond-to-your-signal)):

```
connect  →  [0xA4, 0x3C]  (60-min session guard)  →  loop: [0xA5, duty]
```

> ### Reward-timing note — proportional vs. discrete (operant conditioning)
>
> The ~12 Hz cadence above is a **smoothing rate for a continuously-varying dimmer**, not a reinforcement-latency floor. Two distinct cases:
> - **Proportional feedback** (tint tracks a signal): 12 Hz (~83 ms granularity) is far below the upstream EEG analysis window (typically 250 ms – 1 s+) that dominates the loop, so it is not the limiting term.
> - **Discrete reward** (reinforce the instant a contingency is met): do **not** wait for the next tick — send the `[0xA5, duty]` write immediately. By default `0xA5` applies on-device with **no smoothing** — the breathe slew limiter is `LED_MODE_BREATHE` only — so the tint change is immediate. (Exception: if you have enabled the optional `0xA0` lens smoothing or `0xA1` slew cap on fw ≥ 4.15.7, those **do** apply to static writes and will stretch the transition — leave them off for minimum-latency discrete rewards.) The lens cell then switches in **Ton 2.5–40 ms / Toff 2.5–50 ms** (< 100 ms only when cold). Reward-delivery latency is ~20–60 ms transport + ~40–50 ms lens switch — bounded by your signal processing, not the streaming rate.
>
> The SDK `FeedbackStream` exposes this split directly: `feed()` / `feed_reward()` for the proportional stream, `reward_event(duty, hold_ms)` for an immediate, tick-bypassing discrete reward that preempts the stream (waiting at most one in-flight write).

> **On connect** the lens is already running a standalone program until your first write takes over — normally Breathe at the **NVS-persisted** `0xB1` rate (factory default 6 BPM; a previous client's setting survives, [§4.3](#43-control-characteristic-0xff01--command-opcodes)), or a strobe program if a magnet tap already cycled it ([§4.1.1](#411-standalone-programs--magnet-gestures)). Don't treat "6 BPM breathe" as a connect-time invariant.
>
> **`0xA5` and `0xA2` write the SAME firmware variable** (`brightness`): `0xA5` = enter static mode + set the level (not persisted); `0xA2` = set the same value AND persist it. There is **no** separate ceiling clamping `0xA5`. Side effect: the last streamed `0xA5` value becomes the breathe **depth** if you later switch to breathe mode without re-sending `0xA2`.

#### 4.6.2 Command the renderer — don't stream PWM

There are two ways to make the lens "breathe" (fade clear → dark → clear). (This is about *periodic* waveforms — for an aperiodic feedback signal, streaming is the right tool: [§4.6.1](#461-continuous-opacity-feedback--the-biofeedback-pattern).)

- **❌ Stream per-tick PWM.** Compute the waveform value every frame and write it as a static duty (`0xA5`, or the 1-byte opacity write) at ~12 Hz. Don't: it's a continuous write stream (BLE air-time + glasses power), and any link jitter or a dropped write shows up as a visible stutter — smoothness becomes hostage to the link.
- **✅ Command the breathe program.** Send a handful of parameter writes **once** and the glasses render the smooth **100 Hz cosine locally**. The link carries only occasional writes; the waveform is smooth regardless of BLE conditions. This is the "fade clear→dark over the inhale, dark→clear over the exhale, repeating" primitive — the firmware owns the interpolation.

> There is **no one-shot "ramp A→B over T seconds" opcode.** Breathe mode *is* the timed fade, but it is **cyclic** (repeats every breath). For a steady, non-breathing tint that just tracks a slow value, use `0xA5` as a **setpoint you refresh ~1 Hz** — not a per-frame stream. For fast continuous feedback (e.g. EEG-driven opacity), stream `[0xA5, duty]` or the 1-byte opacity write per the biofeedback pattern in [§4.6.1](#461-continuous-opacity-feedback--the-biofeedback-pattern).

#### 4.6.3 The breathe op set

| Opcode | Arg | Meaning |
|---|---|---|
| `0xB0` | `0x00` | enter BREATHE mode (firmware renders the cosine from here on) |
| `0xB1` | bpm 1–30 | breathe rate — **integer BPM** (see §4.6.5) |
| `0xB2` | pct 10–90 | inhale fraction (40 = inhale 40 % / exhale 60 %) |
| `0xA2` | pct 0–100 | amplitude / depth — peak lens darkness at full inhale (your feedback → depth map) |
| `0xBA` | `[cycle_ms u16 LE, 2000–30000][inhale_pct u8, 10–90]` (out-of-range silently clamped) | **phase-lock + exact cycle** (fw ≥ 4.15.5) — a **per-breath keep-alive**: the exact-cycle override auto-expires 2 cycles after the last write, so re-send at every breath boundary (see §4.6.5) |
| `0xA5` | pct 0–100 | STATIC mode — immediate tint; use as a slow setpoint, not a stream |

> **Recommended envelope.** The firmware accepts `0xB1` 1–30 BPM and `0xB2` 10–90 % inhale, but production clinical clients constrain to **4–20 BPM** and **30–70 %** inhale — treat that as the recommended envelope.

JS (the `0xBA` write; the drive layer coalesces `0xB0`/`0xB1`/`0xB2`/`0xA2`, writing an opcode only when its value changed, ~1 Hz, and calls `syncBreath` at each breath boundary):

```js
async function syncBreath(chCtrl, cycleMs, inhalePct) {
  const c   = Math.max(2000, Math.min(30000, Math.round(cycleMs)));
  const inh = Math.max(10, Math.min(90, Math.round(inhalePct)));
  await sendCtrlCommand(chCtrl, 0xBA, new Uint8Array([c & 0xff, (c >> 8) & 0xff, inh]));
}
```

Python equivalent of the per-breath push (call **only at a breath boundary** — see §4.6.5):

```python
async def push_breath(client, cycle_ms: int, inhale_pct: int, depth_pct: int):
    bpm = max(1, min(30, round(60_000 / cycle_ms)))
    await client.write_gatt_char(
        EDGE_CTRL, struct.pack('<BHB', 0xBA, cycle_ms, inhale_pct), response=True)  # exact cycle + phase anchor
    await send_cmd(client, 0xB1, bpm)         # integer-rate fallback for old firmware
    await send_cmd(client, 0xB2, inhale_pct)
    await send_cmd(client, 0xA2, depth_pct)   # depth for THIS breath
```

#### 4.6.4 Lens opacity is not linear — the duty→opacity floor (fw ≥ 4.15.4)

The electrochromic cell shows **no visible tint below ~26 % drive**, so the firmware remaps your duty onto the *visible* range: **duty 0 → fully clear; duty 1..100 → raw [265..1023]** (duty 1 is already the first visible step, ~26 % electrically). Consequences for your feedback → `0xA2` / `0xA5` mapping:

- `depth = 0` is the only fully-clear value. `depth = 1` is **already visibly tinted** — the bottom of the range is a **hard step**, not a fade-in.
- Usable contrast is `1..100`. Treat `0` as "off"; map your feedback signal onto `1..100` (or a floor like `8..100` if you want the lens to always show *something*).

#### 4.6.5 Phase sync — the one rule: write only at the breath boundary

The firmware's breathe phase is **free-running**: `t = (tick_count × 10 ms) mod cycle_ms`, and nothing resets it. So the glasses' inhale/exhale boundaries fall at **arbitrary times** relative to your on-screen breathing cue and your audio chime — they drift apart. And `0xB1` is **integer BPM**, so a fractional pacer (e.g. 5.4 br/min) rounds, adding a rate mismatch.

**`0xBA BREATHE_SYNC` `[cycle_ms u16 LE][inhale_pct u8]`** fixes both: it **restarts the cosine at the instant of the write** (phase origin = now = start of inhale) **and** sets the **exact** cycle length in ms (within the 2–30 s valid window — out-of-range values are silently clamped, [§4.3](#43-control-characteristic-0xff01--command-opcodes)).

> **The exact-cycle override is a per-breath keep-alive, not persistent state.** The firmware renders at the `0xBA` cycle length only while the last sync write is at most **2 cycles** old; after that it auto-expires back to the integer `0xB1` rate (`60000 / bpm`) — so a client that sends `0xBA` once (or only on rate changes) silently reverts to the rounded integer BPM within two breaths, reintroducing exactly the rate mismatch this section exists to fix. Re-sending `0xBA` at every breath boundary is therefore **required** to hold a fractional rate — the per-boundary push in the code samples below is functionally mandatory, not just snap-avoidance. (The expiry is deliberate: it lets magnet/hall-button breathe revert cleanly once the app stops syncing. It is time-based, so it applies while connected too, not just after a disconnect.)

> ### ⚠️ Send `0xBA` (and any `0xB1` / `0xA2` change) **only at the breath-cycle boundary** — never mid-breath
>
> Two independent reasons:
> 1. **The correction is invisible at the seam.** Re-anchor when *your* clock is at the start of inhale (waveform ≈ 0, lens at its clearest). Both clocks are at the same point, so the phase fix is a visual no-op. Re-anchoring mid-inhale teleports the firmware's phase → a visible snap.
> 2. **Mid-breath param changes warp the waveform.** The firmware recomputes `effective_duty = wave(frac) × depth` every 10 ms from the *live* params. Change the rate (→ `cycle_ms` / `inhale_ms`) or the depth (`0xA2`) mid-inhale and `frac` or the product moves **non-monotonically** — the lens darkens, clears a bit, then darkens again. (This exact stutter is a bug that shipped once, then was fixed by moving to boundary-only writes.)
>
> **So: latch your breathe params per breath.** Sample rate + depth once at each boundary, hold them for the whole breath, and push (`0xBA` + `0xB1` + `0xA2`) only at the next boundary.

The firmware also **slew-rate-limits** the breathe tint (≤ 4 %/10 ms ≈ 250 ms full-scale fade) as belt-and-suspenders, so the one unavoidable correction — the very first anchor, a deliberate rate snap, or a reconnect — *fades* instead of snapping. You do nothing for this; just know it exists, and that it does **not** apply to strobe.

**Drive the on-screen cue and the audio chime off the SAME app breath clock** as your `0xBA` writes. Because the lens is phase-locked to that clock, screen + sound + physical lens all line up — including as your pacer rate drifts.

Python — a minimal boundary-latched breath driver:

```python
class BreathDriver:
    """The app owns one breath clock; screen cue, audio chime, and 0xBA writes all read it."""

    def __init__(self, client):
        self.client = client
        self.cycle_ms = 10_000
        self.pending_cycle_ms = 10_000   # your engine updates these continuously…
        self.pending_depth = 0           # …but they are APPLIED only at the boundary.
        self.phase = 0.0                 # ∈ [0, 1), advanced by elapsed time

    async def start(self):
        await send_cmd(self.client, 0xB0)                                   # enter breathe (once)
        await push_breath(self.client, self.cycle_ms, 40, self.pending_depth)  # initial anchor

    async def tick(self, dt_ms: float):
        self.phase += dt_ms / self.cycle_ms
        if self.phase >= 1.0:                       # ← BREATH BOUNDARY (frac ≈ 0)
            self.phase -= 1.0
            self.cycle_ms = self.pending_cycle_ms   # re-sample rate ONCE, here
            await push_breath(self.client, self.cycle_ms, 40, self.pending_depth)
        update_on_screen_cue(self.phase)            # smooth, app-side
        maybe_play_chime(self.phase)                # inhale/exhale edges off the same clock
        # NOTHING about rate/depth/cycle is written to the glasses between boundaries.
```

JS — the boundary push:

```js
onBreathBoundary() {              // fires once per breath, at frac ≈ 0
  this.cycleMs = this.pacer.latch();
  this.latchLensParams();         // sample depth + rate ONCE, here (held for the whole breath)
  this.emitLens();                // → 0xB0 / 0xB1 / 0xB2 / 0xA2 (coalesced)
  this.emitSync();                // → syncBreath: 0xBA cycle_ms + inhale%
}
// Between boundaries, nothing rate/depth/cycle is sent; the on-screen cue + chime read
// the same breath clock the 0xBA writes anchor.
```

#### 4.6.6 Strobe

| Opcode | Arg | Meaning |
|---|---|---|
| `0xA6` | `0x00` | enter STROBE mode |
| `0xAB` | see below | strobe frequency |
| `0xAC` | pct 10–90 | dark fraction of each strobe period |

`0xAB` has **two wire forms** — use the deci-Hz form for sub-Hz precision (entrainment targets like 13.5 / 17.5 Hz):

```
integer Hz (2 bytes):        [0xAB, hz]                    hz = 1–50
deci-Hz    (3 bytes, prefer): [0xAB, dHz_lo, dHz_hi]        dHz = round(hz × 10), u16 LE
```

Python:

```python
async def set_strobe_hz(client, hz: float):
    dhz = round(max(1.0, min(50.0, hz)) * 10)               # e.g. 13.5 Hz → 135
    await client.write_gatt_char(EDGE_CTRL, struct.pack('<BH', 0xAB, dhz), response=True)

await set_strobe_hz(client, 13.5)
await send_cmd(client, 0xAC, 50)   # dark duty 50%
await send_cmd(client, 0xA6)       # start strobe → [0xA6, 0x00]
```

JS:

```js
async function setStrobeFreqHz(chCtrl, hz) {
  const dhz = Math.round(Math.max(1, Math.min(50, hz)) * 10);   // e.g. 13.5 Hz → 135
  await sendCtrlCommand(chCtrl, 0xAB, new Uint8Array([dhz & 0xff, (dhz >> 8) & 0xff]));
}
// enter strobe: sendCtrlCommand(chCtrl, 0xA6);  set dark duty: sendCtrlCommand(chCtrl, 0xAC, new Uint8Array([pct]));
```

> **Strobe needs hard edges — never smooth, slew, or ramp it.** Write the params once and let the firmware's ISR toggle. (The breathe slew limiter in §4.6.5 deliberately does not touch strobe.)
>
> **Breathe + strobe** (a strobe whose dark-duty is modulated by the breathing wave) is entered with **`0xB0 0x01`** (fw ≥ 4.15.6): the breathe arg selects the variant — `0xB0 0x00` = plain breathe, `0xB0 0x01` = breathe+strobe. It stays phase-locked to `0xBA` / `0xB1` / `0xB2` exactly like plain breathe, and toggling the arg `0↔1` preserves the breathe phase. On firmware **< 4.15.6** there is no standalone breathe+strobe opcode — fall back to pure breathe (`0xB0`) **or** pure strobe (`0xA6`).

#### 4.6.7 Backward compatibility & version

`0xBA` is **ignored by firmware < 4.15.5** (unknown opcode → silent no-op), so you can always send it; on old firmware the lens stays on the integer-`0xB1` rate path (rate-matched, not phase-locked) — and on current firmware the lens likewise falls back to that same integer path **2 cycles after the last `0xBA`** ([§4.6.5](#465-phase-sync--the-one-rule-write-only-at-the-breath-boundary)), which is why `0xB1` must always accompany `0xBA`. **The Edge does not expose a Device Information Service** and has no version characteristic — the only version readback is the informal `0xF1` hello string emitted on `0xFF03` subscribe (`Narbis fw v…`, fw ≥ 4.12.1; [§4.4.2](#442-log-string-0xf1--variable)); on older firmware there is no readback at all, so send `0xBA` unconditionally (it's safe) rather than gating on version. (The *earclip* does expose DIS `0x180A` / `0x2A26` — [§3.4](#34-device-information-service--0x180a) — but that is the earclip's version, not the Edge's.) The full feature/version matrix is in [§9.3](#93-firmware-featureversion-matrix).

### 4.7 The Edge as relay

> ### ⚠️ Relay disabled on stock firmware
>
> Current glasses builds ship with **`EARCLIP_CENTRAL_ENABLED 0`** — the central radio stays dark, eliminating the ~80 mA active-scan drain when no earclip is in use. Everything in this section (pairing, the relay frames `0xF4`–`0xF9`, and the relay-control opcodes `0xC1` / `0xC3` / `0xC4` / `0xC5`) is implemented but **inert** unless the firmware is rebuilt with the flag set to `1`. On stock builds, use **Pattern A** (direct earclip connection) for earclip data, or feed beats to the legacy pipeline via `0xCA` ([§4.8](#48-legacy-on-board-coherence-pipeline-unused)).

The Edge is not just a peripheral — it can also run a NimBLE **central** that scans for, pairs with, and persistently reconnects to the earclip. Once linked, the Edge transparently forwards earclip notifications to whichever client is connected to it.

```
                                  ┌─────────────────┐
                                  │    Your app     │
                                  │ (Python / web)  │
                                  └────────┬────────┘
                                           │ BLE (peripheral role on Edge)
                                           │   • 0xFF01 commands (incl. 0xC1/C3/C4)
                                           │   • 0xFF03 status + relayed 0xF4..0xF9
                                           │   • 0xFF04 PPG stream (not emitted on current fw — §4.5)
                                           │
                                  ┌────────▼────────┐
                                  │  Narbis Edge    │
                                  │  ESP32 dual-role│
                                  └────────┬────────┘
                                           │ BLE (central role on Edge,
                                           │      writes PEER_ROLE = GLASSES = 2)
                                           │
                                  ┌────────▼────────┐
                                  │ Narbis Earclip  │
                                  │  ESP32-C6       │
                                  │  (multi-central)│
                                  └─────────────────┘
```

**Pairing.** First boot or after a `0xC1` (or 5 magnet-taps), the Edge scans generally for the earclip's NARBIS service UUID, picks the strongest hit, and stores the MAC in NVS. Subsequent boots do a directed scan for that MAC (30 s scan window, ~5 s backoff between attempts), repeating indefinitely — there is no automatic fallback to general scan while a MAC is persisted. To pair a different earclip (e.g. a replacement unit), send `0xC1` (or 5 magnet-taps) to forget the stored MAC; general scan runs only when no MAC is stored.

**What the Edge subscribes to.** When linked, the Edge keeps live subscriptions to the earclip's IBI, BATTERY, CONFIG, RAW_PPG (if enabled via `0xC4`), and DIAGNOSTICS characteristics. Every notification is relayed to the client-facing `0xFF03` with the appropriate type byte:

| Earclip characteristic | Relay frame on `0xFF03` |
|---|---|
| IBI | `0xF9` (binary, 5 B — §4.4.10) **and** `0xF1` text log mirror |
| BATTERY | `0xF8` (binary, 5 B — §4.4.9) **and** `0xF1` text log mirror |
| CONFIG | `0xF4` (§4.4.5) |
| RAW_PPG | `0xF5` (§4.4.6) — opt-in via `0xC4` |
| DIAGNOSTICS | `0xF7` (§4.4.8) |

In addition the Edge emits its own `0xF6` relay-link-state, `0xFA` link-quality, and `0xF3` health frames so the client can render connection-strength UI without managing a second BLE link.

**Per-beat IBI is fully relayed** (since glasses fw 4.15.2): subscribe to `0xFF03`, watch for `0xF9`, and you get every detected beat with confidence + flags. The earclip-direct option (Pattern A) is only needed if you want the raw IBI characteristic without the Edge in the path at all (e.g. for timing-sensitive HRV recording during a glasses OTA).

#### Integration choice

| Pattern | When to use it | Trade-offs |
|---|---|---|
| **A. Direct (two connections)** — connect to both the earclip and the Edge separately. | You want lowest-latency raw IBI for your own pipeline; you want full control of earclip config. **The only option on stock glasses builds.** | Two BLE connections to manage. |
| **B. Single connection via Edge** — connect only to the Edge; consume `0xF4`–`0xF9` from `0xFF03`. **Requires a relay-enabled build** (`EARCLIP_CENTRAL_ENABLED 1`). | You're building a companion app that mainly drives Edge sessions; earclip data via the relay is enough. | One fewer connection. Slight extra latency on relayed frames (one BLE hop). Earclip config writes go through the Edge via `0xC3` — currently broken by the 50-B/74-B version mismatch ([§4.3 `0xC3` row](#43-control-characteristic-0xff01--command-opcodes)); use a direct earclip connection for config until the relay is rebuilt against config v4. |

JS — status dispatch + relay-control writes (frame layouts in §4.4):

```js
switch (bytes[0]) {                                                       // 0xFF03 multiplex
  case 0xF6: emit('centralRelayState', { connected: bytes[1] !== 0 }); break;
  case 0xF9: emit('relayedIbi',     { ibi_ms: bytes[1]|(bytes[2]<<8), confidence_x100: bytes[3], flags: bytes[4] }); break;
  case 0xF8: emit('relayedBattery', { mv: bytes[1]|(bytes[2]<<8), soc_pct: bytes[3], charging: bytes[4] }); break;
  case 0xF4: emit('relayedConfig',     { bytes: bytes.slice(1) }); break; // 74 B → deserializeConfig (§3.6 / §5)
  case 0xF5: emit('relayedRawPpg',     { bytes: bytes.slice(1) }); break;
  case 0xF7: emit('relayedDiagnostic', { bytes: bytes.slice(1) }); break; // → diagnostics parser (§3.1.9)
}
// relay-control writes:
sendCtrlCommand(chCtrl, 0xC3, payload);  // forward a CONFIG_WRITE — ⚠️ glasses forward only 50 B (v3-era); see the §4.3 0xC3 version-mismatch note
sendCtrlCommand(chCtrl, 0xC4, new Uint8Array([on ? 1 : 0])); // toggle raw-PPG relay
sendCtrlCommand(chCtrl, 0xC1);                               // forget earclip pairing
```

### 4.8 Legacy: on-board coherence pipeline (unused)

The firmware retains a complete on-board HRV-coherence pipeline: it collects IBIs (from the earclip relay on relay-enabled builds, or injected via `0xCA`), runs band-power analysis on a fixed FFT grid, produces the `0xF2` coherence packet ([§4.4.3](#443-coherence-packet-0xf2--18-b)), and can drive the lens itself through four **PPG programs**. Current apps do not use any of this — they compute their feedback app-side and drive the lens via [§4.6](#46-driving-the-edge-lens). The opcodes remain live on the wire and are documented here for completeness and for thin clients that want the glasses to do everything.

**Minimal use** (per connect): `[0xCB, 0x01]` to declare the app the beat authority, `[0xB7, n]` to pick a program, then one 5-byte `[0xCA][ibi_ms:u16 LE][confidence][flags]` write per beat (write-with-response — `0xFF01` does not expose write-without-response, [§4.2](#42-service-0x00ff--characteristic-map); the firmware drops beats with `confidence < conf_threshold`, default 50, or `flags & 0x01` ARTIFACT). Coherence results arrive as `0xF2` frames at 1 Hz. `0xCB` is not persisted — re-assert on every connect.

**PPG programs** (`0xB7`, not persisted):

| Arg | Program | Lens behaviour |
|---|---|---|
| 0 | heartbeat | pulse on every beat (same effect as `0xB6`) |
| 1 | coh-breathe | lens follows the firmware's breathing pacer |
| 2 | coh-lens | lens opacity tracks the firmware coherence score directly |
| 3 | coh-breathe-strobe | pacer + strobe |

**`0xE0` — coherence pipeline params.** Single write replacing the entire `narbis_coh_params_t`: `[0xE0]` + 12 raw struct bytes = 13 B, no CRC. Persists to NVS. On validation failure the firmware silently drops the write and emits a `0xE0 reject` line on `0xF1`. All `*_lo`/`*_hi` fields are FFT bin indices on a fixed 4 Hz × 256-point grid (`df = 0.015625 Hz/bin`; e.g. bin 3 ≈ 0.04 Hz, bin 10 ≈ 0.15 Hz, bin 26 ≈ 0.40 Hz).

| Offset | Size | Field | Default | Range | Controls |
|---|---|---|---|---|---|
| 0 | 1 | `min_ibis` | 20 | 5–120 | Min beats before coherence is computed |
| 1 | 1 | `conf_threshold` | 50 | 0–100 | Beats below this confidence are dropped at the `0xCA` / earclip entry points |
| 2 | 1 | `vlf_band_lo` | 1 | 0–127 | VLF band integration, inclusive lo |
| 3 | 1 | `vlf_band_hi` | 2 | ≥ lo, ≤ 127 | VLF hi |
| 4 | 1 | `lf_band_lo` | 3 | 0–127 | LF band lo |
| 5 | 1 | `lf_band_hi` | 9 | ≥ lo, ≤ 127 | LF hi |
| 6 | 1 | `hf_band_lo` | 10 | 0–127 | HF band lo |
| 7 | 1 | `hf_band_hi` | 25 | ≥ lo, ≤ 127 | HF hi |
| 8 | 1 | `lf_peak_lo` | 3 | 0–127 | LF peak-search window lo |
| 9 | 1 | `lf_peak_hi` | 9 | ≥ lo, ≤ 127 | LF peak hi |
| 10 | 1 | `peak_halfwidth` | 0 | 0–8 | `0` = single-bin peak; `N` = sum peak ± N bins |
| 11 | 1 | `coh_multiplier` | 100 | 10–255 | Score scaling (was 250 pre-fw 4.14.31 — old presets hard-coding 250 will under-shoot) |

There is **no read-back characteristic** for the current params, and no echo over BLE at boot or on (re)connect — at boot the active values go to the serial console only. The only wire echo is the `0xE0 ok` line on `0xF1` after an accepted write, and it reports only the LF/HF bands, peak window ± halfwidth, and multiplier (not `min_ibis`, `conf_threshold`, or the VLF band). Clients must shadow the full 12-byte param set locally.

**`0xB8` — difficulty preset** (persisted): selects a gamma exponent applied to the coherence-to-lens-opacity mapping in the coh-lens program (`0xB0` arg 2 / PPG program 2) only: `lens_clear_pct = (coh/100)^gamma × 100`. `0` Easy gamma 1.0 (linear, historical), `1` Medium 1.5, `2` Hard 2.0, `3` Expert 3.0; args > 3 are ignored. All curves meet at coh = 0 (dark) and coh = 100 (fully clear) — no dead zones (e.g. at coh = 50 the lens is 50/35/25/13 % clear for Easy/Medium/Hard/Expert). Difficulty does **NOT** touch the coherence score itself — the score is computed identically at every difficulty (same formula, same LF peak-search range), so scores are comparable across sessions and difficulty levels; do not rescale scores per difficulty. `0xB8` is fully orthogonal to `0xE0`: it writes no pipeline parameter and `0xE0` writes no difficulty — the two can be combined freely.

**`0xB9` — adaptive pacer toggle** (persisted): `1` = the pacer's target BPM walks slowly toward the user's measured resonant respiratory frequency (0.2 BPM steps, slew-limited); `0` = fixed 6.0 BPM pacer (hard-coded — `0xB1`/`0xB2` have no effect on the coherence pacer in either mode; it always uses a 40/60 inhale/exhale split). Only takes effect at the next cycle boundary of PPG programs 1 / 3. The current target is exposed as `pacer_rate_q5` (byte 17, quintets = BPM × 5) in the `0xF2` frame — divide by 5 for BPM.

**`0xD0`** resets the firmware's beat-detection state; **`0xB6`** enters pulse-on-beat mode directly.

---

## 5. Configuring the earclip

End-to-end: write PEER_ROLE first, then read CONFIG, change a field, write CONFIG_WRITE. The only non-obvious part of the wire format is the CRC.

### 5.1 CRC-16-CCITT-FALSE

Poly `0x1021`, init `0xFFFF`, no reflect, no xor-out — over the 72 struct bytes; the 2 CRC bytes are appended little-endian.

Python:

```python
def narbis_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc
```

JS:

```js
function narbisCrc16(buf, len) {
  let crc = 0xFFFF;
  for (let i = 0; i < len; i++) {
    crc ^= buf[i] << 8;
    for (let b = 0; b < 8; b++) crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
  }
  return crc & 0xFFFF;
}
```

### 5.2 Python — full config serializer + round-trip write

The `struct` format string below encodes the exact [§3.6](#36-the-runtime-config-struct) layout (all little-endian, byte-packed):

```python
import struct
from dataclasses import dataclass, astuple

CONFIG_FMT = '<HHHHBBHIIHHHHHHHHHBBBHBBBHBBBBHHHHHBBHHHBB'
assert struct.calcsize(CONFIG_FMT) == 72

@dataclass
class NarbisConfig:
    config_version: int = 4        # firmware-owned — your value is ignored on write
    sample_rate_hz: int = 200
    led_red_ma_x10: int = 70
    led_ir_ma_x10: int = 70
    agc_enabled: int = 1
    reserved_agc: int = 0
    agc_update_period_ms: int = 200
    agc_target_dc_min: int = 30000    # firmware default — NOT range-checked on write
    agc_target_dc_max: int = 100000   # firmware default — NOT range-checked on write
    agc_step_ma_x10: int = 5          # firmware default — NOT range-checked on write
    bandpass_low_hz_x100: int = 50
    bandpass_high_hz_x100: int = 800
    elgendi_w1_ms: int = 111
    elgendi_w2_ms: int = 667
    elgendi_beta_x1000: int = 20
    sqi_threshold_x100: int = 50
    ibi_min_ms: int = 300
    ibi_max_ms: int = 2000
    ibi_max_delta_pct: int = 30
    ble_profile: int = 0           # BATCHED
    data_format: int = 0           # IBI_ONLY
    ble_batch_period_ms: int = 500
    diagnostics_enabled: int = 1
    light_sleep_enabled: int = 1
    diagnostics_mask: int = 0
    battery_low_mv: int = 3300
    # ---- v4 adaptive detector ----
    detector_mode: int = 0         # FIXED (= legacy Elgendi pipeline)
    template_max_beats: int = 10
    template_warmup_beats: int = 4
    kalman_warmup_beats: int = 5
    template_window_ms: int = 200
    ncc_min_x1000: int = 500
    ncc_learn_min_x1000: int = 750
    kalman_q_ms2: int = 400
    kalman_r_ms2: int = 2500
    kalman_sigma_x10: int = 30
    watchdog_max_consec_rejects: int = 5
    watchdog_silence_ms: int = 4000
    alpha_min_x1000: int = 10
    alpha_max_x1000: int = 500
    # ---- v4 auxiliary ----
    elgendi_loose_mode: int = 0
    refractory_ibi_pct: int = 60

def serialize_config(cfg: NarbisConfig) -> bytes:
    body = struct.pack(CONFIG_FMT, *astuple(cfg))
    return body + struct.pack('<H', narbis_crc16(body))       # 74 B on the wire

# ⚠️ The firmware does NOT range-check agc_target_dc_min / agc_target_dc_max /
# agc_step_ma_x10 — writing zeros silently breaks AGC. Prefer read-modify-write
# (read CONFIG, change only what you need, write back) over building from defaults.

def deserialize_config(blob: bytes) -> NarbisConfig:
    body = bytes(blob[:72])
    crc, = struct.unpack_from('<H', blob, 72)
    if narbis_crc16(body) != crc:
        raise ValueError('CONFIG CRC mismatch')
    return NarbisConfig(*struct.unpack(CONFIG_FMT, body))
```

Round-trip against a live earclip:

```python
CONFIG_UUID       = '553abc98-6406-4e37-b9fd-34df85b2b6c1'
CONFIG_WRITE_UUID = '129fbe56-cbd6-4f52-957b-d80834d6abf3'
PEER_ROLE_UUID    = 'e987719a-26a6-48d4-b8e9-128994e62e6c'

async with BleakClient(earclip_device) as client:
    # 0. ALWAYS write your role first. 1 = DASHBOARD → LOW_LATENCY profile.
    await client.write_gatt_char(PEER_ROLE_UUID, bytes([0x01]), response=True)

    # 1. Read + parse current config (74 B = 72 struct + 2 CRC).
    cfg = deserialize_config(await client.read_gatt_char(CONFIG_UUID))

    # 2. Change what you need; the firmware validates ranges on write.
    cfg.ble_profile = 1        # LOW_LATENCY
    cfg.data_format = 2        # IBI_PLUS_RAW

    # 3. Write back. A bad CRC / out-of-range field raises (ATT error on the write).
    await client.write_gatt_char(CONFIG_WRITE_UUID, serialize_config(cfg), response=True)
```

### 5.3 JS

```js
async function writeConfig(chConfigWrite, cfg) {
  const blob = serializeConfig(cfg);   // 74 B incl. CRC — write each field little-endian at its
  await chConfigWrite.writeValueWithResponse(blob);   // §3.6 offset, then setUint16(72, crc, true)
}
// serializeConfig(cfg): into a 74-byte buffer, write one DataView.setUintN per §3.6 table row,
// in table order, then: view.setUint16(72, narbisCrc16(buf, 72), true);
// deserializeConfig is the inverse (and verifies the CRC) — use it for CONFIG reads/notifies too.
```

### 5.4 Cheap alternative — the MODE write

For just changing the data format / profile, the 2-byte MODE write is much cheaper than a full config round-trip:

```python
await client.write_gatt_char(MODE_UUID, bytes([1, 2]), response=True)
#                                              ^  ^
#                                              |  data_format = IBI_PLUS_RAW
#                                              ble_profile   = LOW_LATENCY
```

```js
await chMode.writeValueWithResponse(new Uint8Array([1, 2])); // [ble_profile=LOW_LATENCY, data_format=IBI_PLUS_RAW]
```

The earclip notifies on the CONFIG characteristic with the updated 74-byte payload after either write succeeds — subscribe to it once at startup so your app state stays in sync.

---

## 6. OTA — shared between both devices

The data/status/page-handshake protocol is **identical** on Edge and earclip — the earclip's OTA was deliberately ported from the Edge so a single updater core handles both. Two per-device differences remain: **control-write lengths** — the earclip accepts ONLY exactly-2-byte writes on `0xFF01` (anything else returns an ATT invalid-attribute-value-length error), so the `[0xA8, size:u32 LE]` fast-erase START is **Edge-only** ([§6.3](#63-opcodes-write-to-0xff01-as-opcode-param)) — and **simple-status frame padding** ([§6.4](#64-status-notifications-read-from-0xff03)). The earclip adds three safety gates (battery, chip-ID, re-entry) on top.

Always disambiguate by name first; the OTA service UUID `0x00FF` lives on both devices.

### 6.1 Service & characteristics

| Role | UUID | Properties | Notes |
|---|---|---|---|
| Service | `0x00FF` | — | Same UUID on both devices |
| Control | `0xFF01` | read + write | Opcodes (this is also the Edge command char — same UUID) |
| Data | `0xFF02` | write + write-no-response | Firmware bytes in chunks ≤ 244 B |
| Status | `0xFF03` | read + notify | Status frames 1–7 B |

### 6.2 Constants

| Constant | Value |
|---|---|
| Page size | 4096 B (one flash erase block) |
| Recommended chunk size | **244 B** (`MTU − 3` at the negotiated MTU 247). Both devices accept up to `MTU − 3` per OTA-data write. |
| Page CRC | standard CRC-32 (poly `0xEDB88320` — `zlib.crc32` in Python); computed by the device and sent **big-endian** in the PAGE_CRC notification ([§6.4](#64-status-notifications-read-from-0xff03)) |

### 6.3 Opcodes (write to `0xFF01` as `[opcode, param]`)

| Opcode | Name | Param | Meaning |
|---|---|---|---|
| `0xA8` | START | **Edge:** `[image_size:u32 LE]` (preferred) or `0x00`. **Earclip:** exactly `[0xA8, 0x00]` | Enter OTA mode; device responds with `READY`. **Edge:** with the size (len ≥ 5), the device erases only an image-sized flash region — much faster begin, shorter radio stall; `[0xA8, 0x00]` = legacy full-slot erase (~1.5 MB — the slow path the 32 s supervision timeout exists to survive). **Earclip:** a 5-byte START is **rejected** (ATT invalid-attribute-value-length — the earclip accepts only exactly-2-byte control writes), and there is no image-sized-erase path at all: it always calls `esp_ota_begin(OTA_SIZE_UNKNOWN)` |
| `0xA9` | FINISH | `0x00` | Write any trailing partial page, flush, set boot partition, reboot |
| `0xAA` | CANCEL | `0x00` | Abort transfer |
| `0xAD` | PAGE_CONFIRM | `0x01` commit / `0x00` resend | Driven by client after verifying PAGE_CRC |

### 6.4 Status notifications (read from `0xFF03`)

| First byte | Name | Length (Edge) | Payload |
|---|---|---|---|
| `0x01` | READY | 1 B | `01` — earclip: `[01 00 00 00]` (4 B, zero-padded)\* |
| `0x02` | PROGRESS | 4 B | `02` + 3 progress bytes — **Edge-only / reserved**: the earclip never emits it |
| `0x03` | SUCCESS | 1 B | `03` — reboot is imminent, expect disconnect. Earclip: `[03 00 00 00]`\* |
| `0x04` | ERROR | 2 B | `04 <err>` — earclip: `[04 err 00 00]`\* |
| `0x05` | CANCELLED | 1 B | `05` — earclip: `[05 00 00 00]`\* |
| `0x06` | PAGE_CRC | 7 B | `06 page_hi page_lo crc32_be[4]` — client must verify and ack |
| `0x07` | PAGE_OK | 3 B | `07 page_hi page_lo` — page committed to flash |
| `0x08` | PAGE_RESEND | 3 B | `08 page_hi page_lo` — restart this page |

> \* The **earclip zero-pads the simple statuses to 4 B** (READY / SUCCESS / CANCELLED / ERROR as shown); the 1 B / 2 B lengths apply to the **Edge only**.

Status frames range from **1 to 7 bytes on the Edge, 3 to 7 bytes on the earclip** — **dispatch on byte 0 only; never assert exact frame length.**

> **Byte-order quirk:** the page-status frames (`0x06`/`0x07`/`0x08`) pack the page number **and** the CRC32 **big-endian** (MSB first) — the one part of the protocol that is not little-endian. The PAGE_CRC wire frame is `{0x06, page>>8, page&0xFF, crc>>24, crc>>16, crc>>8, crc&0xFF}`. The CRC *algorithm* is standard CRC-32 (poly `0xEDB88320`, == Python `zlib.crc32`) — only the wire byte order is big-endian.

> **PAGE_RESEND rewinds the whole page.** On `[0xAD, 0x00]` the firmware discards the buffered page (rewinds the full 4096 B) — the client must resend the **entire page from its start**, not just the tail.

> **Stop-and-wait is mandatory — two silent-failure rules.** (1) Any `0xFF02` data written between a page filling (PAGE_CRC) and the page's PAGE_OK / PAGE_RESEND is **silently dropped** — no ERROR notify, nothing on the wire; do not pipeline the next page while verifying the CRC. (2) Never let a single write straddle a 4096-B page boundary: when the page fills mid-write, the excess bytes are **discarded** (4096 = 16 × 244 + 192, so the 17th chunk of a page must be 192 B — the example loops in [§6.8](#68-python--complete-ota-loop-bleak)/[§6.9](#69-js--condensed-loop-web-bluetooth) get this right by slicing per page). Both losses surface only later as a CRC mismatch or corrupt image.

A PAGE_CRC fires only when a full 4096-B page fills; a trailing **partial** page gets **no per-page handshake** — it is written during FINISH (`0xA9`). See [§6.8](#68-python--complete-ota-loop-bleak).

### 6.5 Error codes (byte 1 of an `0x04 ERROR` notification)

| Code | Name | Both? | Meaning |
|---|---|---|---|
| `0x01` | BEGIN | both | `esp_ota_begin()` failed |
| `0x02` | WRITE | both | `esp_ota_write()` failed during page commit |
| `0x03` | END | both | `esp_ota_end()` or `set_boot_partition()` failed |
| `0x04` | NOT_IN_OTA | both | Data received outside an OTA session |
| `0x05` | NO_PARTITION | both | `esp_ota_get_next_update_partition()` returned NULL |
| `0x06` | LOW_BATTERY | earclip only | SoC < 30 % — charge before OTA |
| `0x07` | CHIP_MISMATCH | earclip only | Image is not for ESP32-C6 |
| `0x08` | ALREADY_IN_OTA | earclip only | START received during an OTA session |

> Always handle the earclip-only codes even when targeting the Edge — they simply won't fire there. Future-proofing is cheap.

### 6.6 State machine

```
   client                              device
   ──────                              ──────
   subscribe to STATUS  ─────────────►
   write [0xA8][size:u32 LE] (Edge) ►      (earclip: exactly [0xA8, 0x00] — 2 B only;
                                            Edge legacy: [0xA8, 0x00] = full-slot erase)
                        ◄──────── notify [0x01]   READY (1 B Edge / 4 B earclip)

   ┌── for each FULL 4096-B page ─────┐
   │  write data in 244-B chunks  ──► │
   │  (write-no-response, 17 chunks)
   │                  ◄──── notify [0x06, page# BE, crc32 BE]
   │  verify CRC32
   │  write [0xAD, 0x01]   ─────────► │     (commit)
   │                  ◄──── notify [0x07, page# BE]
   │  …or if CRC mismatched:          │
   │  write [0xAD, 0x00]   ─────────► │     (page discarded — resend it all)
   │                  ◄──── notify [0x08, page# BE]
   └──────────────────────────────────┘

   write trailing partial page (< 4096 B) — no PAGE_CRC handshake
   write [0xA9, 0x00]   ─────────────►
                        ◄──────── notify [0x03]   SUCCESS (1 B Edge / 4 B earclip)
   (device reboots; you'll get a disconnect)
```

No `0xFF02` writes are allowed inside the CRC/confirm window (between a page filling and its PAGE_OK / PAGE_RESEND) — they are **silently dropped** ([§6.4](#64-status-notifications-read-from-0xff03)).

### 6.7 Firmware image header — pre-flight validation

Before sending the first byte, validate that the `.bin` you're about to ship matches the device. The header is in the first 32 bytes of the image:

| Offset | Size | Field | Expected value |
|---|---|---|---|
| 0x00 | 1 | image magic | `0xE9` |
| 0x01 | 1 | segment_count | — |
| 0x0C | 2 | `chip_id` (LE) | `0x0000` for ESP32 (Edge), `0x000D` for ESP32-C6 (earclip) |

The `app_desc` (ESP-IDF `esp_app_desc_t`) sits at fixed file offset `0x20` — immediately after the 24-byte image header and the 8-byte first-segment header, i.e. at the **start** of the first segment payload. Magic `0xABCD5432` (u32 LE) is at `0x20`; the NUL-terminated `version[32]` starts at `0x30` (16 bytes after the magic starts — `secure_version` and 8 reserved bytes sit in between, so do **NOT** read the version right after the magic); `project_name[32]` follows at `0x50`. Parse `version` at `0x30` to show the user "updating to vX.Y.Z" before flashing.

```python
def validate_image(image: bytes, expect_c6: bool) -> str:
    assert image[0] == 0xE9, 'not an ESP32 app image'
    chip_id, = struct.unpack_from('<H', image, 0x0C)
    assert chip_id == (0x000D if expect_c6 else 0x0000), f'wrong chip_id 0x{chip_id:04x}'
    assert struct.unpack_from('<I', image, 0x20)[0] == 0xABCD5432, 'app_desc magic missing'
    return image[0x30:0x50].split(b'\0')[0].decode()   # version, e.g. '4.15.6'
```

### 6.8 Python — complete OTA loop (bleak)

```python
import asyncio, struct, zlib
from bleak import BleakClient

PAGE_SIZE, CHUNK_SIZE = 4096, 244

async def ota_update(client: BleakClient, image: bytes, is_edge: bool = True):
    status_q: asyncio.Queue = asyncio.Queue()

    def on_status(_char, data: bytearray):
        if data and data[0] <= 0x08:              # OTA frames only; 0xF0.. telemetry keeps flowing on Edge
            status_q.put_nowait(bytes(data))

    async def expect(timeout: float) -> bytes:
        frame = await asyncio.wait_for(status_q.get(), timeout)
        if frame[0] == 0x04:
            raise RuntimeError(f'OTA ERROR 0x{frame[1]:02x}')   # see §6.5
        return frame

    # 0. Subscribe BEFORE the first opcode or you'll miss READY / PAGE_CRC.
    await client.start_notify(EDGE_STATUS, on_status)

    # 1. START — Edge: 5-byte [0xA8][image_size:u32 LE]; the device erases only an
    #    image-sized flash region (fast begin). Legacy [0xA8, 0x00] erases the full
    #    ~1.5 MB slot: the radio can block for tens of seconds — the reason the
    #    supervision timeout is 32 s. Earclip: control writes must be EXACTLY
    #    2 bytes — a 5-byte START is rejected (ATT invalid-length BleakError), and
    #    there is no image-sized-erase path (esp_ota_begin(OTA_SIZE_UNKNOWN) always).
    start = struct.pack('<BI', 0xA8, len(image)) if is_edge else bytes([0xA8, 0x00])
    await client.write_gatt_char(EDGE_CTRL, start, response=True)
    frame = await expect(timeout=40.0)
    assert frame[0] == 0x01, f'expected READY, got {frame.hex()}'   # READY: 1 B on Edge, 4 B zero-padded on earclip

    # 2. Page loop — only FULL 4096-B pages get the PAGE_CRC handshake (§6.4).
    n_full = len(image) // PAGE_SIZE
    page_num = 0
    while page_num < n_full:
        page = image[page_num * PAGE_SIZE:(page_num + 1) * PAGE_SIZE]
        for off in range(0, PAGE_SIZE, CHUNK_SIZE):
            await client.write_gatt_char(EDGE_DATA, page[off:off + CHUNK_SIZE], response=False)

        frame = await expect(timeout=15.0)                     # PAGE_CRC (7 B)
        assert frame[0] == 0x06, f'expected PAGE_CRC, got {frame.hex()}'
        dev_page = (frame[1] << 8) | frame[2]                  # page number is BIG-endian (§6.4)
        dev_crc, = struct.unpack_from('>I', frame, 3)          # CRC32 is BIG-endian too (§6.4)
        ok = dev_page == page_num and dev_crc == (zlib.crc32(page) & 0xFFFFFFFF)
        await client.write_gatt_char(EDGE_CTRL, bytes([0xAD, 0x01 if ok else 0x00]), response=True)

        frame = await expect(timeout=15.0)                     # PAGE_OK or PAGE_RESEND (3 B)
        if frame[0] == 0x07:
            page_num += 1                                      # committed → next page
        elif frame[0] == 0x08:
            pass                                               # page discarded — loop resends the WHOLE page
        else:
            raise RuntimeError(f'unexpected OTA frame {frame.hex()}')
        print(f'\rOTA {page_num}/{n_full} full pages', end='')

    # 3. Trailing partial page — written with NO per-page handshake (§6.4);
    #    the device commits it during FINISH.
    tail = image[n_full * PAGE_SIZE:]
    for off in range(0, len(tail), CHUNK_SIZE):
        await client.write_gatt_char(EDGE_DATA, tail[off:off + CHUNK_SIZE], response=False)

    # 4. FINISH — device flushes the tail, sets the boot partition, and reboots
    #    (expect a disconnect). Dispatch on byte 0: 0x03 SUCCESS (1 B on Edge,
    #    4 B zero-padded on earclip); ERROR = [0x04, err] (+2 pad bytes on earclip).
    await client.write_gatt_char(EDGE_CTRL, bytes([0xA9, 0x00]), response=True)
    frame = await expect(timeout=30.0)
    assert frame[0] == 0x03, f'expected SUCCESS, got {frame.hex()}'
```

### 6.9 JS — condensed loop (Web Bluetooth)

```js
const CHUNK_SIZE = 244, PAGE_SIZE = 4096;
function crc32(data) { /* standard CRC-32, poly 0xEDB88320 — table-driven */ }

async function sendChunks(cD /* 0xFF02 */, data) {
  for (let i = 0; i * CHUNK_SIZE < data.length; i++) {
    await cD.writeValueWithoutResponse(data.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE));
  }
}
// 1. START — branch on device type (the earclip accepts ONLY exactly-2-byte control
//    writes: a 5-byte START rejects with an ATT invalid-length error, and the earclip
//    has no image-sized-erase path at all):
//      Edge:    const start = new DataView(new ArrayBuffer(5));
//               start.setUint8(0, 0xA8); start.setUint32(1, image.byteLength, true);  // u32 LE size
//               ([0xA8, 0x00] = Edge legacy full-slot erase)
//      Earclip: const start = new Uint8Array([0xA8, 0x00]);
//      await cC.writeValueWithResponse(start);   → wait for byte 0 === 0x01 READY
//                                                  (1 B on Edge, 4 B zero-padded on earclip)
// 2. per FULL 4 KB page: sendChunks(cD, page) → wait for 7-byte 0x06 PAGE_CRC, then:
//      const devCrc = frameDv.getUint32(3, false);             // page# AND CRC32 are BIG-endian (§6.4)
//      devCrc === crc32(page) ? cC.write([0xAD, 0x01]) /*commit*/ : cC.write([0xAD, 0x00]) /*resend*/
//    → 0x07 PAGE_OK (next page) | 0x08 PAGE_RESEND (page discarded — resend the WHOLE page)
// 3. trailing partial page: sendChunks(cD, tail) — no PAGE_CRC handshake for a partial page
// 4. cC.write([0xA9, 0x00]) FINISH → byte 0 === 0x03 SUCCESS (1 B on Edge, 4 B zero-padded on
//    earclip; device reboots) | byte 0 === 0x04 ERROR: [0x04, err] (2 B Edge / 4 B earclip)
```

> **Do NOT treat a 5–10 second silence on `0xFF03` mid-OTA as a stall.** When the Edge erases its update partition at OTA begin, the radio can be blocked — with the legacy full-slot erase (`[0xA8, 0x00]`, ~1.5 MB) for 6–19 s, and **well past 19 seconds** on worn flash. The size-hinted `0xA8` start ([§6.3](#63-opcodes-write-to-0xff01-as-opcode-param)) erases only an image-sized region and shortens the stall considerably, but the **32-second** supervision timeout (the BLE max) is set to survive the slow path. Don't disconnect until you've waited at least 35 s with no progress. (This applies to both devices — the earclip's `OTA_SIZE_UNKNOWN` begin can stall the same way.)

---

## 7. Client gotchas

### 7.1 Web Bluetooth gotchas

The wire protocol is identical across clients, but Web Bluetooth (Chrome / Edge browsers) has its own rules — the JS snippets in this doc are battle-tested against all of these:

- **User gesture required.** `navigator.bluetooth.requestDevice()` must be called from a click/tap handler — you **cannot** auto-connect on page load or reconnect silently. There is no passive scan; the browser shows a device chooser, one device per call.
- **`optionalServices` is mandatory.** Any service you call `getPrimaryService()` on must be listed in a `filter` **or** in `optionalServices` at `requestDevice()` time, or the call throws `SecurityError`. Include the custom Narbis service and any SIG service (`0x180d` / `0x180f` / `0x180a`) you read. (Exact filters: [§2.2](#22-javascript-web-bluetooth).) This is the #1 footgun.
- **No RSSI.** Web Bluetooth never exposes RSSI to JS — which is exactly why the Edge ships link RSSI up in the `0xFA` frame ([§4.4.11](#4411-link-quality-0xfa--7-b)). Drive signal-strength UI from that, not the BLE API.
- **No MTU API.** No way to read the negotiated MTU; `writeValue()` fragments for you. Keep writes ≤ 244 B and don't worry about it ([§2.4](#24-mtu)).
- **Foreground only.** A backgrounded/hidden tab throttles timers and can stop delivering notifications — there is no Web Bluetooth background mode. Expect drop-outs when the tab isn't visible and re-anchor any beat/sample clocks after a gap.
- **Browser support.** Chrome / Edge / Brave on desktop + Android only. **No Firefox, no Safari, no iOS browser** (iOS has no Web Bluetooth).
- **Permission caching + `device.forget()`.** The browser caches an accepted device (~30 s after disconnect) and re-matches it on the next `requestDevice()` without prompting. If a device's GATT cache goes stale (the "needs multiple Forget+Connect cycles" symptom), call `device.forget()` (Chrome 114+) to release the grant.
- **`writeValueWithResponse` vs `writeValueWithoutResponse`.** On these devices, write-without-response exists **only on the `0xFF02` OTA data characteristic** — reserve `writeValueWithoutResponse` for OTA data chunks. Everything else — all `0xFF01` commands, the `0xA5` duty stream, per-beat `0xCA` on the legacy path, and the earclip's CONFIG_WRITE/MODE/PEER_ROLE — must use `writeValueWithResponse`: `0xFF01` does not advertise the write-without-response property, and Web Bluetooth rejects `writeValueWithoutResponse` with `NotSupportedError` when the property bit is absent.
- **One write at a time.** A `writeValue*` call rejects if another GATT operation on the characteristic is still in flight — serialize commands **and** the duty stream through one promise queue (the serialization rule in [§4.3](#43-control-characteristic-0xff01--command-opcodes)).

```js
// Everything starts from a user gesture:
button.addEventListener('click', async () => {
  const device = await navigator.bluetooth.requestDevice({ filters: [/*…*/], optionalServices: [/*every service you read*/] });
  const server = await device.gatt.connect();
  // …getPrimaryService / getCharacteristic / startNotifications…
});
```

### 7.2 Windows / WinRT & Python (bleak) gotchas

- **WinRT rejects overlapping GATT writes** ("operation already in progress") — serialize all writes to `0xFF01`, including the duty stream (the serialization rule in [§4.3](#43-control-characteristic-0xff01--command-opcodes)).
- **Windows may strip 16-bit service UUIDs from advertisements** entirely — always filter by device name, never by service UUID ([§2.2](#22-javascript-web-bluetooth)).
- **Windows / WinRT scan cadence.** The WinRT backend surfaces advertisements in batches, and a freshly woken Edge (100–200 ms advertising interval) can take several seconds to appear. Scan with `timeout=10–15 s` and retry once before telling the user to tap the magnet — a 3-second scan will produce false "not found" results.
- **Notify callbacks must not block.** bleak invokes your notification callback on the event loop (it accepts both sync and async callables). Never sleep or do heavy work inside it — push the payload onto an `asyncio.Queue` and process elsewhere. This is exactly the pattern the OTA loop in [§6.8](#68-python--complete-ota-loop-bleak) uses.
- **`response=True` for control writes.** Always write the Edge control characteristic (`0xFF01`) and earclip CONFIG_WRITE/MODE/PEER_ROLE with `response=True`: you get ordering, back-pressure, and an exception when the earclip rejects a bad config. Reserve `response=False` for the `0xFF02` OTA data chunks **only** — the one characteristic that exposes write-without-response. `0xFF01` does not, so `response=False` there (including the `0xA5` duty stream and per-beat `0xCA`) raises NotSupported on BlueZ and is unreliable on WinRT.
- **Reconnecting after the 2-minute teardown.** Once the Edge powers its radio down, `BleakClient.connect()` against a cached `BLEDevice` just times out. Catch the timeout/`BleakError`, prompt the user to tap the magnet, and go back to **scanning** — don't retry connect in a loop against the stale device object.
- **Use full 128-bit UUID strings.** bleak wants `'0000ff01-0000-1000-8000-00805f9b34fb'`, not `0xFF01` — expand 16-bit UUIDs with the SIG base as in [§2.1](#21-python-bleak).
- **One characteristic, one subscription.** The Edge's `0xFF03` multiplexes telemetry, relay frames, and OTA status; register a single dispatcher callback ([§4.4](#44-status-characteristic-0xff03--notification-multiplexer)) rather than starting/stopping notify around each operation.

---

## 8. Troubleshooting matrix

| Symptom | Likely cause | Fix |
|---|---|---|
| Edge not in scan results but powered | Either the 2-minute idle teardown (radio fully powered down), **or another app already connected** — advertising stops on connect, so a second central can't discover the Edge ([§4.1](#41-advertising--connection-parameters)) | Tap the magnet to re-arm advertising; make sure no other client (e.g. the Narbis app/dashboard) holds the connection |
| Both devices appear with the same service UUID | They share `0x00FF` for OTA | Disambiguate by advertised name |
| Command write appears to do nothing, lens goes to a random tint instead | Wrote 1 byte — interpreted as the legacy opacity command | Pad every opcode to ≥ 2 bytes ([§4.3](#43-control-characteristic-0xff01--command-opcodes)) |
| OTA fails immediately on earclip with err `0x06` | Battery SoC < 30 % | Charge the earclip before retry |
| OTA fails on earclip with err `0x07` | Wrong `.bin` (Edge image targeted at earclip) | Validate the chip-id field (offset `0x0C`) before sending ([§6.7](#67-firmware-image-header--pre-flight-validation)) |
| Connection drops mid-OTA | Client disconnected before the OTA-begin partition erase finished | Don't disconnect for at least 35 s after last progress; the supervision timeout is 32 s for this reason |
| MODE write succeeds but format unchanged | Wrote to the wrong characteristic UUID | Verify you're using `71db6de8-…` (earclip) — not `0xFF01` |
| Notifications stop after a few seconds | Forgot to enable the CCCD | `start_notify()` (bleak) / `startNotifications()` (Web Bluetooth) for every notify characteristic |
| Garbled CONFIG read | Skipped CRC verification | Validate the last 2 B with CRC-16-CCITT-FALSE over the first `len − 2` bytes (72 B for v4, 48 B for v3) |
| No `0xF2` coherence frames arrive at all (while other `0xFF03` traffic flows) | The legacy pipeline isn't receiving beats — on stock builds the earclip central is compile-disabled ([§4.7](#47-the-edge-as-relay)) | Feed beats via `0xCA` if you're using the legacy pipeline ([§4.8](#48-legacy-on-board-coherence-pipeline-unused)) — or ignore `0xF2` entirely; app-side processing doesn't need it. On a relay-enabled build: check the latest `0xF6` frame; if `linked=0`, send `0xC1` and let the Edge re-pair |
| `0xF4`/`0xF5` frames never arrive even with `0xF6 linked=1` | Earclip has no skin contact / no signal | Confirm with earclip BATTERY notify or SQI; the raw stream needs `0xC4 1` enabled |
| Client stuck at BATCHED notify cadence even after writing PEER_ROLE | Wrote PEER_ROLE *after* enabling notifies | Re-order: write `[0x01]` to PEER_ROLE first, then subscribe to IBI |
| Earclip CONFIG read returns 74 B but parser expects 50 B (or 58 B) | Parser stuck on an older `config_version` | Branch on `config_version` (offset 0): `≤2` legacy 56-B struct, `3` 48-B struct, `4` 72-B struct. The first 48 bytes of v3 and v4 are identical, so a v3-only parser can read everything it knows from a v4 frame by ignoring bytes 48..71 |
| Health telemetry `ble_send_errors` climbing | Device notifications failing to send (congested/degraded link) — the counter tracks failed device→client NOTIFY sends, not client writes | Check link quality/RSSI and connection interval; ensure the client is connected and subscribed and the link isn't saturated |
| Lens mode changes mid-session without any app write (e.g. sudden 10 Hz strobing) | A magnet tap cycled the standalone program — gestures stay live while a client is connected ([§4.1.1](#411-standalone-programs--magnet-gestures)) | Watch `led_mode` (byte 20) in the 1 Hz `0xF3` frame to detect the takeover and re-assert your mode; instruct users not to tap the magnet mid-session |
| Earclip `firmware_revision` reads as an empty string | Read attempted before service discovery settled | Read DIS strings after the connection is fully established, not immediately on connect |
| RAW_PPG never fires | `data_format` is `IBI_ONLY` | Write MODE to set `data_format = 1` or `2` |
| Lens visibly "steps" on at low feedback values | The duty→opacity floor: duty 1 is already visibly tinted | Map your signal onto `1..100` knowing 0→1 is a hard step ([§4.6.4](#464-lens-opacity-is-not-linear--the-dutyopacity-floor-fw--4154)) |
| Lens stutters mid-breath when the pacer rate changes | `0xBA`/`0xB1`/`0xA2` written mid-breath | Latch params per breath; write only at the cycle boundary ([§4.6.5](#465-phase-sync--the-one-rule-write-only-at-the-breath-boundary)) |
| Lens stops + connection drops after ~30 min | Session timer expired → deep sleep (default 30 min; the clock runs from device wake, and writing `0xA4` does not restart it) | Tap the magnet, re-connect, and write `[0xA4, minutes]` at session start ([§4.1.2](#412-session-auto-sleep--the-0xa4-timer)) |

---

## 9. Reference data

### 9.1 All UUIDs at a glance

```
EARCLIP — Custom Narbis service
  Service        a24080b2-8857-4785-b3ba-a43b66af4f28
  IBI            78ef492f-66be-438d-a91e-ddfdb441b7bb   read* + notify
  SQI            2b614c61-bcdf-4a3f-a7e8-3b5a860c0347   read* + notify
  RAW_PPG        6bacca91-7017-40fa-bb91-4ebf28a65a99   read* + notify
  BATTERY        b59d3ba1-78d1-4260-93c2-7e9e02329777   read* + notify
  CONFIG         553abc98-6406-4e37-b9fd-34df85b2b6c1   read + notify   (74 B in v4; was 50 B in v3)
  CONFIG_WRITE   129fbe56-cbd6-4f52-957b-d80834d6abf3   write           (74 B in v4; was 50 B in v3)
  MODE           71db6de8-5bff-480f-8db1-0d01c90d17d0   write           (2 B)
  PEER_ROLE      e987719a-26a6-48d4-b8e9-128994e62e6c   write           (1 B)
  FACTORY_RESET  c0e221b1-1633-0f9d-364a-7e47a8d9c411   write           (4 B, destructive — §3.1)
  DIAGNOSTICS    31d99572-bf8a-4658-828e-4f7c138ca722   read* + notify

  * read returns a 0-byte value on these — don't poll; only CONFIG returns data on read (§3.1)

EARCLIP — Standard SIG services
  Heart Rate Service                  0x180D
    Heart Rate Measurement            0x2A37   notify + read
    Body Sensor Location              0x2A38   read
  Battery Service                     0x180F
    Battery Level                     0x2A19   read + notify
  Device Information Service          0x180A
    Manufacturer Name                 0x2A29   read
    Model Number                      0x2A24   read
    Hardware Revision                 0x2A27   read
    Firmware Revision                 0x2A26   read
    Serial Number                     0x2A25   read

EDGE — Single custom service
  Service                             0x00FF
    Control                           0xFF01   read + write
                                               opcodes 0xA2..0xE0 (sparse)
                                               0xC1/C3/C4/C5 (relay control — inert on stock builds)
                                               0xCA/CB, 0xE0 (legacy coherence pipeline)
    OTA Data                          0xFF02   write + write-no-response
    Status (multiplexed)              0xFF03   read + notify
                                               own packets 0xF0/F1/F2/F3/FA
                                               relay packets 0xF4..0xF9 (relay-enabled builds)
                                               OTA codes 0x01..0x08
    PPG Stream                        0xFF04   read + notify
                                               (not emitted on current fw — §4.5)

OTA — Shared between Edge and Earclip (same UUIDs)
  Service                             0x00FF
    Control                           0xFF01
    Data                              0xFF02
    Status                            0xFF03
```

128-bit expansions of the 16-bit UUIDs (needed by bleak and Web Bluetooth):

```
0x00FF  →  000000ff-0000-1000-8000-00805f9b34fb
0xFF01  →  0000ff01-0000-1000-8000-00805f9b34fb
0xFF02  →  0000ff02-0000-1000-8000-00805f9b34fb
0xFF03  →  0000ff03-0000-1000-8000-00805f9b34fb
0xFF04  →  0000ff04-0000-1000-8000-00805f9b34fb
```

### 9.2 Wire conventions

- Endianness: **little-endian** everywhere. (Single exception: the OTA page-status frames `0x06`/`0x07`/`0x08` pack the page number **and** the CRC32 big-endian — [§6.4](#64-status-notifications-read-from-0xff03).)
- Structs: byte-packed, no padding except where explicitly named (`reserved_*`).
- Booleans: `u8` with values `0` or `1` — never a C `bool`.
- Fixed-point: scaled integers (`_x10`, `_x100`, `_x1000`) — no float in any wire format.
- CRC for config payloads: **CRC-16-CCITT-FALSE** (poly `0x1021`, init `0xFFFF`, no reflect, no xor-out) — implementations in [§5.1](#51-crc-16-ccitt-false).
- CRC for OTA pages: standard **CRC-32** (poly `0xEDB88320`); sent **big-endian** in the PAGE_CRC frame.

### 9.3 Firmware feature/version matrix

**The Edge exposes no DIS**, so there is no version characteristic to read — but firmware ≥ 4.12.1 announces its build as a `0xF1` log frame on `0xFF03` the moment you enable status notifications: `Narbis fw v<version> test=<0|1> mode=<n>` (e.g. `Narbis fw v4.15.6-strobe-sync test=0 mode=0`). Parse the token after `v` — take its leading `major.minor.patch` numeric prefix; a build suffix like `-strobe-sync` may follow ([§4.4.2](#442-log-string-0xf1--variable)). Use that line for version gating; if it never arrives (fw < 4.12.1), fall back to sending version-gated opcodes unconditionally and relying on graceful fallback ([§4.6.7](#467-backward-compatibility--version)).

| Feature | Minimum glasses fw | Behaviour on older firmware |
|---|---|---|
| `0xA0`/`0xA1`/`0xA3` lens-config knobs (smoothing / slew cap / disconnect fail-clear) | 4.15.7 | ignored (unknown opcodes) — lens snaps, no slew cap, disconnect freezes at last output |
| `0xBA` breathe-sync | 4.15.5 | ignored (unknown opcode) |
| `0xB0 0x01` breathe+strobe | 4.15.6 | plain breathe |
| duty→opacity floor remap ([§4.6.4](#464-lens-opacity-is-not-linear--the-dutyopacity-floor-fw--4154)) | 4.15.4 | no floor remap |
| `0xF3` led bytes (`led_mode` / `led_duty`) | 4.15.3 (late builds only — added mid-4.15.3, commit 049105c) | 20-byte frame without them; discriminate by frame length (20 vs 22 B), not by version |
| `0xFA` link quality | 4.15.3 | not emitted |
| `0xF9` per-beat relay | 4.15.2 | not emitted |
| `0xAB` 3-byte deci-Hz form | 4.14.41 | only the integer-Hz 2-byte form |
