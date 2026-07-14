/**
 * EDGE Glasses JavaScript/TypeScript SDK
 * Control smart LCD glasses over Web Bluetooth
 *
 * The glasses are a display: your app computes its biofeedback signal
 * (EEG alpha, HRV, GSR, ...) and drives the lens by commanding the
 * firmware's breathe / static / strobe renderer.
 *
 * @module edge-glasses
 * @version 2.2.0
 */

// BLE UUIDs
const SERVICE_UUID = 0x00ff;
const CHAR_UUID = 0xff01;
const DEVICE_NAME = 'Narbis_Edge';

/**
 * Breathe waveform shapes (opcode 0xB5).
 */
export const Waveform = {
  /** Cosine-shaped tint curve (device default) */
  Sine: 0,
  /** Linear (triangle) tint curve */
  Linear: 1,
} as const;
export type Waveform = (typeof Waveform)[keyof typeof Waveform];

/**
 * Options for {@link Glasses.startBreathe}.
 * Only the parameters you provide are written; everything else keeps
 * its current (NVS-persisted) value on the device.
 */
export interface BreatheOptions {
  /** Breathing rate, 1-30 BPM (integer). Persisted. */
  bpm?: number;
  /** Inhale portion of the cycle, 10-90 %. Persisted. */
  inhalePct?: number;
  /** Hold at top of inhale, 0-5000 ms (100 ms resolution). Persisted. */
  holdTopMs?: number;
  /** Hold at bottom of exhale, 0-5000 ms (100 ms resolution). Persisted. */
  holdBottomMs?: number;
  /** Tint curve shape: Waveform.Sine (0) or Waveform.Linear (1). Persisted. */
  waveform?: Waveform;
  /**
   * If true, start breathe+strobe mode: the strobe's dark-phase duty is
   * modulated by the breathing waveform (firmware >= 4.15.6).
   * If false/omitted, plain breathe mode.
   */
  withStrobe?: boolean;
}

/**
 * Scan result from BLE discovery
 */
export interface ScanResult {
  device: BluetoothDevice;
  name: string;
}

/** Clamp helper: integer clamp into [min, max]. */
function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Math.floor(value)));
}

/**
 * EDGE Smart Glasses Controller
 *
 * All commands go over a single control characteristic (0xFF01) with
 * write-with-response. The firmware never NACKs: out-of-range arguments
 * are silently clamped or dropped on the device, so this SDK validates
 * and clamps everything client-side before sending.
 *
 * Note: the glasses power the radio down after 2 minutes with no client
 * connected. If the device does not appear in the chooser, tap the magnet
 * to the temple briefly to re-arm advertising.
 *
 * @example
 * ```typescript
 * const glasses = new Glasses();
 * await glasses.connect();
 * await glasses.setOpacity(128);          // 50% dark, static
 * await glasses.startBreathe({ bpm: 6 }); // on-board breathe engine
 * ```
 */
export class Glasses {
  private device: BluetoothDevice | null = null;
  private server: BluetoothRemoteGATTServer | null = null;
  private characteristic: BluetoothRemoteGATTCharacteristic | null = null;
  private _connected = false;

  /**
   * Check if currently connected
   */
  get isConnected(): boolean {
    return this._connected && this.server?.connected === true;
  }

  /**
   * Get device name
   */
  get deviceName(): string | undefined {
    return this.device?.name;
  }

  // -------------------------------------------------------------------------
  // Connection Management
  // -------------------------------------------------------------------------

  /**
   * Request and connect to EDGE Glasses.
   * Uses the Web Bluetooth API - must be called from a user gesture.
   *
   * Filters on the exact advertised name `Narbis_Edge`, with a fallback
   * filter on the control service (0x00FF).
   *
   * The glasses stop advertising and power the radio down after 2 minutes
   * with no client connected. If nothing shows up in the device chooser,
   * tap the magnet to the temple to wake the radio, then try again.
   *
   * @throws Error if Bluetooth is unavailable or the connection fails
   */
  async connect(): Promise<void> {
    if (!navigator.bluetooth) {
      throw new Error('Web Bluetooth not supported. Use Chrome/Edge on desktop or Android.');
    }

    try {
      // Request device (exact-name match, service UUID as fallback)
      this.device = await navigator.bluetooth.requestDevice({
        filters: [{ name: DEVICE_NAME }, { services: [SERVICE_UUID] }],
        optionalServices: [SERVICE_UUID]
      });

      if (!this.device.gatt) {
        throw new Error('GATT not available on device');
      }

      // Connect to GATT server
      this.server = await this.device.gatt.connect();

      // Get service and characteristic
      const service = await this.server.getPrimaryService(SERVICE_UUID);
      this.characteristic = await service.getCharacteristic(CHAR_UUID);

      this._connected = true;

      // Handle disconnection
      this.device.addEventListener('gattserverdisconnected', () => {
        this._connected = false;
        console.log('EDGE Glasses disconnected');
      });

    } catch (error) {
      this._connected = false;
      throw new Error(
        `Connection failed: ${error}. ` +
        'If the glasses were not found, they may have gone idle - ' +
        'tap the magnet to the temple to re-arm advertising.'
      );
    }
  }

  /**
   * Disconnect from glasses
   */
  disconnect(): void {
    if (this.server?.connected) {
      this.server.disconnect();
    }
    this._connected = false;
    this.device = null;
    this.server = null;
    this.characteristic = null;
  }

  // -------------------------------------------------------------------------
  // Low-level Commands
  // -------------------------------------------------------------------------

  /**
   * Write raw bytes to the control characteristic.
   * No padding - used internally by setOpacity() for the 1-byte legacy write.
   */
  private async writeRaw(data: number[]): Promise<void> {
    if (!this.isConnected || !this.characteristic) {
      throw new Error('Not connected. Call connect() first.');
    }

    const buffer = new Uint8Array(data);
    await this.characteristic.writeValueWithResponse(buffer);
  }

  /**
   * Send an opcode command, enforcing the >= 2-byte rule.
   *
   * A 1-byte write is interpreted by the firmware as the legacy opacity
   * command, so any argument-less opcode is padded to [opcode, 0x00].
   */
  private async send(data: number[]): Promise<void> {
    if (data.length < 2) {
      data = [...data, 0x00];
    }
    await this.writeRaw(data);
  }

  /**
   * Send a raw opcode command (low-level escape hatch).
   *
   * Pads the total write to at least 2 bytes ([opcode, 0x00]) so it is
   * never misread as the 1-byte legacy opacity command.
   *
   * The firmware never NACKs - invalid opcodes/arguments are silently
   * dropped or clamped on the device, so validate values yourself.
   *
   * @param opcode Command opcode byte (e.g. 0xA2)
   * @param payload Optional argument bytes
   */
  async sendCommand(opcode: number, payload: number[] = []): Promise<void> {
    await this.send([opcode & 0xff, ...payload.map(b => b & 0xff)]);
  }

  // -------------------------------------------------------------------------
  // Simple Control (Legacy 1-byte write)
  // -------------------------------------------------------------------------

  /**
   * Set static lens opacity via the 1-byte legacy write.
   * Stops any running mode and holds a static tint.
   *
   * This is the ONE command that is intentionally a single byte on the
   * wire - the firmware treats any 1-byte write as opacity. Fine to
   * stream at ~12 Hz for continuous biofeedback (~20 Hz is the tolerated ceiling).
   *
   * Value is clamped to 0-255 client-side (firmware never NACKs).
   *
   * @param value Opacity 0-255 (0 = fully clear, 255 = fully dark)
   */
  async setOpacity(value: number): Promise<void> {
    value = clamp(value, 0, 255);
    await this.writeRaw([value]);
  }

  /**
   * Set lenses to fully clear (transparent).
   */
  async clear(): Promise<void> {
    await this.setOpacity(0);
  }

  /**
   * Set lenses to fully dark (opaque).
   */
  async dark(): Promise<void> {
    await this.setOpacity(255);
  }

  /**
   * Enter static mode at a fixed duty cycle (opcode 0xA5).
   * Stops any running mode. Not persisted.
   *
   * Duty 1-100 % maps to a perceptually-floored raw range on the device
   * (fw >= 4.15.4); 0 = fully clear. Clamped to 0-100 client-side
   * (firmware never NACKs).
   *
   * @param duty Duty cycle 0-100 %
   */
  async setStatic(duty: number): Promise<void> {
    duty = clamp(duty, 0, 100);
    await this.send([0xA5, duty]);
  }

  // -------------------------------------------------------------------------
  // Device Settings
  // -------------------------------------------------------------------------

  /**
   * Set the lens level / breathe depth (opcode 0xA2). Persisted in NVS.
   * Writes the SAME firmware variable as setStatic() — it is not a ceiling
   * that clamps later setStatic() writes; a later setStatic() overwrites it.
   *
   * Clamped to 0-100 client-side (firmware never NACKs).
   *
   * @param percent Brightness 0-100 %
   */
  async setBrightness(percent: number): Promise<void> {
    percent = clamp(percent, 0, 100);
    await this.send([0xA2, percent]);
  }

  /**
   * Set session duration (opcode 0xA4). Persisted in NVS.
   * The glasses auto-sleep when the duration elapses.
   *
   * Clamped to 1-60 client-side (firmware never NACKs).
   *
   * @param minutes Session length 1-60 minutes
   */
  async setDuration(minutes: number): Promise<void> {
    minutes = clamp(minutes, 1, 60);
    await this.send([0xA4, minutes]);
  }

  // -------------------------------------------------------------------------
  // Lens Config (firmware >= 4.15.7; older firmware ignores these)
  // -------------------------------------------------------------------------

  /**
   * Set on-device lens smoothing (opcode 0xA0). Persisted in NVS.
   *
   * The firmware glides between commanded static targets (setStatic /
   * setOpacity / the disconnect fail-clear) with an EMA of this time
   * constant, so a low-rate or lossy feedback stream renders as smooth
   * motion instead of steps. Rule of thumb: 1-2x your write period
   * (12 Hz stream -> 80-160 ms). Breathe/strobe waveforms unaffected.
   *
   * Requires fw >= 4.15.7; older firmware ignores the write, so it is
   * always safe to call.
   *
   * @param ms Time constant 0-2550 ms (10 ms resolution). 0 = off (snap).
   */
  async setLensSmoothing(ms: number): Promise<void> {
    const tau = clamp(ms / 10, 0, 255);
    await this.send([0xA0, tau]);
  }

  /**
   * Cap how fast the lens may transition (opcode 0xA1). Persisted in NVS.
   *
   * A hard slew limit on commanded static transitions, applied after the
   * smoothing glide - a safety envelope that guarantees the lens cannot
   * snap even if a host streams garbage. 40 corresponds to full-scale in
   * ~250 ms (the breathe engine's own internal limit). Breathe/strobe
   * waveforms unaffected.
   *
   * Requires fw >= 4.15.7; older firmware ignores the write.
   *
   * @param percentPer100ms Max change 0-100 %/100ms. 0 = unlimited (default).
   */
  async setLensMaxRate(percentPer100ms: number): Promise<void> {
    const rate = clamp(percentPer100ms, 0, 100);
    await this.send([0xA1, rate]);
  }

  /**
   * Choose what the lens does when the BLE link drops (opcode 0xA3).
   * Persisted in NVS.
   *
   * Factory default (false): the lens FREEZES at its last commanded output
   * across a disconnect - a crashed app leaves the last tint in place.
   * With `failClear: true` the glasses instead stop any strobe and drop to
   * a clear static lens on link loss (riding the setLensSmoothing glide if
   * configured).
   *
   * The failsafe fires when the firmware declares the link dead, bounded
   * by the ~20 s supervision timeout - still send an explicit clear()
   * before an intentional disconnect.
   *
   * Requires fw >= 4.15.7; older firmware ignores the write.
   *
   * @param failClear true = go clear on disconnect, false = continue the
   *   running program (factory default)
   */
  async setDisconnectBehavior(failClear: boolean): Promise<void> {
    await this.send([0xA3, failClear ? 0x01 : 0x00]);
  }

  // -------------------------------------------------------------------------
  // Strobe Mode
  // -------------------------------------------------------------------------

  /**
   * Set strobe frequency (opcode 0xAB). Persisted in NVS.
   * Does not start strobing - see {@link startStrobe}.
   *
   * Clamped to 1-50 client-side (firmware never NACKs).
   *
   * @param hz Strobe frequency 1-50 Hz
   */
  async setStrobeFrequency(hz: number): Promise<void> {
    hz = clamp(hz, 1, 50);
    await this.send([0xAB, hz]);
  }

  /**
   * Set strobe duty cycle (opcode 0xAC). Persisted in NVS.
   * Does not start strobing - see {@link startStrobe}.
   *
   * Clamped to 10-90 client-side (firmware never NACKs).
   *
   * @param percent Dark-phase duty 10-90 %
   */
  async setStrobeDuty(percent: number): Promise<void> {
    percent = clamp(percent, 10, 90);
    await this.send([0xAC, percent]);
  }

  /**
   * Start strobe mode (opcode 0xA6), optionally setting frequency and
   * duty first. Omitted parameters keep their current persisted values.
   *
   * @param hz Optional strobe frequency 1-50 Hz
   * @param dutyPct Optional dark-phase duty 10-90 %
   */
  async startStrobe(hz?: number, dutyPct?: number): Promise<void> {
    if (hz !== undefined) {
      await this.setStrobeFrequency(hz);
    }
    if (dutyPct !== undefined) {
      await this.setStrobeDuty(dutyPct);
    }
    await this.send([0xA6, 0x00]);
  }

  // -------------------------------------------------------------------------
  // Breathe Mode
  // -------------------------------------------------------------------------

  /**
   * Start the on-board breathe engine (opcode 0xB0), optionally setting
   * breathing parameters first. Only the options you provide are written;
   * everything else keeps its current persisted value.
   *
   * With `withStrobe: true` the glasses run breathe+strobe: a strobe whose
   * dark-phase duty is modulated by the breathing waveform (fw >= 4.15.6).
   *
   * All values are clamped client-side (firmware never NACKs):
   * bpm 1-30, inhalePct 10-90, holds 0-5000 ms (sent in 100 ms units).
   *
   * @param options Breathing parameters, all optional
   */
  async startBreathe(options: BreatheOptions = {}): Promise<void> {
    const { bpm, inhalePct, holdTopMs, holdBottomMs, waveform, withStrobe } = options;

    if (bpm !== undefined) {
      await this.send([0xB1, clamp(bpm, 1, 30)]);
    }
    if (inhalePct !== undefined) {
      await this.send([0xB2, clamp(inhalePct, 10, 90)]);
    }
    if (holdTopMs !== undefined) {
      await this.send([0xB3, clamp(holdTopMs / 100, 0, 50)]);
    }
    if (holdBottomMs !== undefined) {
      await this.send([0xB4, clamp(holdBottomMs / 100, 0, 50)]);
    }
    if (waveform !== undefined) {
      await this.send([0xB5, clamp(waveform, 0, 1)]);
    }
    await this.send([0xB0, withStrobe ? 0x01 : 0x00]);
  }

  /**
   * Phase-lock the breathe engine to your app's breath pacer
   * (opcode 0xBA, fw >= 4.15.5; silently ignored by older firmware,
   * so it is always safe to send).
   *
   * Restarts the breathe cosine at the instant of the write and sets the
   * EXACT cycle length in milliseconds - this is how you get fractional
   * breathing rates, since the persisted rate (0xB1) is integer-BPM only.
   *
   * IMPORTANT: send this only at the breath-cycle boundary (start of
   * inhale), never mid-breath - each write restarts the waveform, so a
   * mid-cycle sync causes a visible jump. Call it once per breath as your
   * pacer wraps.
   *
   * The sync auto-expires 2 cycles after the last write: if you stop
   * syncing, the engine falls back to its integer-BPM rate.
   *
   * @param cycleMs Exact breath cycle length in ms (u16, little-endian on the wire)
   * @param inhalePct Inhale portion of the cycle, 10-90 % (default 40)
   */
  async syncBreath(cycleMs: number, inhalePct = 40): Promise<void> {
    cycleMs = clamp(cycleMs, 0, 0xffff);
    inhalePct = clamp(inhalePct, 10, 90);
    await this.send([0xBA, cycleMs & 0xff, (cycleMs >> 8) & 0xff, inhalePct]);
  }

  // -------------------------------------------------------------------------
  // Power / Maintenance
  // -------------------------------------------------------------------------

  /**
   * Put the glasses into deep sleep (opcode 0xA7).
   * Wake with a magnet tap.
   */
  async sleep(): Promise<void> {
    await this.send([0xA7, 0x00]);
  }

  /**
   * Restore all persisted settings to factory defaults (opcode 0xBF).
   */
  async factoryReset(): Promise<void> {
    await this.send([0xBF, 0x00]);
  }

  // -------------------------------------------------------------------------
  // Preset Sessions
  // -------------------------------------------------------------------------
  // Presets are fixed-parameter: the firmware no longer ramps frequency or
  // breathing over the session. Each preset configures the renderer, sets
  // the duration (auto-sleep at end), and starts the mode.

  /**
   * Relaxation preset: 5 BPM sine breathe, full brightness.
   *
   * @param minutes Session length 1-60 minutes (default 10)
   */
  async sessionRelax(minutes = 10): Promise<void> {
    await this.setBrightness(100);
    await this.setDuration(minutes);
    await this.startBreathe({ bpm: 5, waveform: Waveform.Sine });
  }

  /**
   * Meditation preset: 6 BPM sine breathe (the device default).
   *
   * @param minutes Session length 1-60 minutes (default 10)
   */
  async sessionMeditate(minutes = 10): Promise<void> {
    await this.setDuration(minutes);
    await this.startBreathe({ bpm: 6, waveform: Waveform.Sine });
  }

  /**
   * Focus preset: breathe+strobe at 12 Hz strobe, 8 BPM breathing.
   *
   * @param minutes Session length 1-60 minutes (default 10)
   */
  async sessionFocus(minutes = 10): Promise<void> {
    await this.setStrobeFrequency(12);
    await this.setDuration(minutes);
    await this.startBreathe({ bpm: 8, withStrobe: true });
  }

  /**
   * Sleep-preparation preset: slow 4 BPM sine breathe.
   *
   * @param minutes Session length 1-60 minutes (default 15)
   */
  async sessionSleep(minutes = 15): Promise<void> {
    await this.setDuration(minutes);
    await this.startBreathe({ bpm: 4, waveform: Waveform.Sine });
  }

  // -------------------------------------------------------------------------
  // Real-time Feedback Streaming
  // -------------------------------------------------------------------------

  /**
   * Open a plug-and-play real-time lens stream (the screen-dimmer pattern).
   *
   * Returns a FeedbackStream: push a value from any callback at any rate via
   * feed() / feedReward(); an internal writer updates the lens at `rateHz`
   * (default ~12 Hz, the production-proven rate; capped at 20 Hz), coalescing
   * unchanged values and keeping exactly one BLE write in flight. Replaces a
   * hand-rolled decimate/coalesce/serialize loop.
   *
   * Proportional feedback (a dimmer that tracks your signal) uses feed() /
   * feedReward(); discrete operant rewards use rewardEvent(), which fires
   * immediately instead of waiting for the next tick.
   *
   * ```ts
   * const stream = glasses.startFeedbackStream();
   * yourPipeline.onUpdate((v) => stream.feedReward(v)); // 0..1, any rate
   * // ...
   * await stream.rewardEvent(0, 150);   // discrete reward, delivered now
   * await stream.stop();                // stops the writer and clears the lens
   * ```
   */
  startFeedbackStream(rateHz = 12): FeedbackStream {
    return new FeedbackStream(this, rateHz);
  }
}

/**
 * Push-style real-time lens control — a wearable screen dimmer.
 *
 * Created via Glasses.startFeedbackStream(). Call feed()/feedReward() from
 * anywhere at any rate; the internal writer decimates to the stream rate,
 * skips unchanged values, and never overlaps BLE writes (a busy guard drops
 * ticks while a write is in flight; a failed write resets the coalesce key
 * so the next tick retries).
 */
export class FeedbackStream {
  private duty: number | null = null;   // latest requested duty 0-100
  private lastSent = -1;
  private inflight: Promise<void> | null = null;   // the one write currently on the wire
  private holdUntil = 0;                // Date.now() until which a reward tint holds
  private timer: ReturnType<typeof setInterval>;

  constructor(private glasses: Glasses, rateHz = 12) {
    const hz = Math.max(1, Math.min(20, rateHz)); // 20 Hz ceiling
    this.timer = setInterval(() => this.tick(), 1000 / hz);
  }

  /**
   * Request a lens duty: 0 = clear … 100 = fully dark. Any call rate is fine.
   * Use this for PROPORTIONAL feedback (a dimmer that tracks your signal).
   */
  feed(duty: number): void {
    this.duty = clamp(Math.round(duty), 0, 100);
  }

  /**
   * Request tint from a 0..1 reward value (1 = in condition = clear).
   * The classic dimmer mapping: duty = (1 - value) * 100.
   */
  feedReward(value: number): void {
    const v = Math.max(0, Math.min(1, value));
    this.feed((1 - v) * 100);
  }

  /**
   * Deliver a DISCRETE reward NOW, bypassing the stream tick.
   *
   * For operant conditioning: call the instant your detector crosses
   * threshold. Unlike feed(), which parks the value for the next scheduled
   * tick (up to one stream period later), this writes immediately — latency
   * is just the BLE transport (~20–60 ms), with no cadence jitter. It
   * preempts the proportional stream, waiting at most one in-flight write.
   *
   * @param duty Reward tint 0–100 (default 0 = fully clear = positive reward)
   * @param holdMs Hold the reward tint this long before the proportional
   *   stream resumes (0 = the next feed() value takes back over immediately)
   */
  async rewardEvent(duty = 0, holdMs = 0): Promise<void> {
    if (this.inflight) { try { await this.inflight; } catch { /* ignore */ } }
    await this.doWrite(clamp(Math.round(duty), 0, 100));
    if (holdMs > 0) this.holdUntil = Date.now() + holdMs;
  }

  /** Serialized single write: only one setStatic() is ever on the wire. */
  private doWrite(duty: number): Promise<void> {
    const p = (async () => {
      try { await this.glasses.setStatic(duty); this.lastSent = duty; }
      catch { this.lastSent = -1; }        // failed write: retry next tick
      finally { this.inflight = null; }
    })();
    this.inflight = p;
    return p;
  }

  private tick(): void {
    if (this.inflight || this.duty === null
        || Date.now() < this.holdUntil || this.duty === this.lastSent) return;
    void this.doWrite(this.duty);
  }

  /**
   * Stop the writer. By default clears the lens — it otherwise FREEZES at
   * the last tint (see the protocol doc, Reconnection).
   */
  async stop(clear = true): Promise<void> {
    clearInterval(this.timer);
    if (clear && this.glasses.isConnected) {
      await this.glasses.clear();
    }
  }
}

// Default export
export default Glasses;
