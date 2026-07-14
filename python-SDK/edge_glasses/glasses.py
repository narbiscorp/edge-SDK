"""
EDGE Glasses - Main SDK module

Targets glasses firmware 4.15.6+ (device name ``Narbis_Edge``).

All biofeedback processing runs app-side: the glasses are a display.
Configure and start the firmware's breathe / static / strobe renderer,
or stream legacy opacity writes for continuous feedback.
"""

import asyncio
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, List
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from .exceptions import (
    ConnectionError,
    DeviceNotFoundError,
    CommandError,
    TimeoutError
)


# BLE UUIDs
SERVICE_UUID = "000000ff-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
DEVICE_NAME = "Narbis_Edge"


class Waveform(IntEnum):
    """Breathe waveform shape (opcode 0xB5)"""
    SINE = 0
    LINEAR = 1


@dataclass
class ScanResult:
    """Represents a discovered EDGE Glasses device"""
    name: str
    address: str
    rssi: int

    def __str__(self):
        return f"{self.name} ({self.address}) RSSI: {self.rssi}"


class Glasses:
    """
    EDGE Smart Glasses controller

    Usage:
        async with Glasses() as glasses:
            await glasses.set_opacity(128)  # 50% dark

    Or manually:
        glasses = Glasses()
        await glasses.connect()
        await glasses.set_opacity(128)
        await glasses.disconnect()

    Note:
        The firmware never NACKs a command - bad arguments are silently
        clamped or dropped on the device. This SDK clamps all arguments
        client-side so what you send is what runs.
    """

    def __init__(self, address: Optional[str] = None):
        """
        Initialize glasses controller

        Args:
            address: Optional BLE address. If None, will scan for device.
        """
        self._address = address
        self._client: Optional[BleakClient] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if currently connected"""
        return self._connected and self._client is not None

    @property
    def address(self) -> Optional[str]:
        """Get the device address"""
        return self._address

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    @staticmethod
    async def scan(timeout: float = 5.0) -> List[ScanResult]:
        """
        Scan for EDGE Glasses devices

        Matches the exact advertised name ``Narbis_Edge``.

        Args:
            timeout: Scan duration in seconds

        Returns:
            List of discovered devices, strongest signal first
        """
        devices = []

        discovered = await BleakScanner.discover(timeout=timeout)
        for d in discovered:
            if d.name == DEVICE_NAME:
                devices.append(ScanResult(
                    name=d.name,
                    address=d.address,
                    rssi=d.rssi or -100
                ))

        return sorted(devices, key=lambda x: x.rssi, reverse=True)

    async def connect(self, timeout: float = 10.0) -> None:
        """
        Connect to glasses

        The glasses stop advertising and fully power down the radio after
        2 minutes with no client connected. If the device can't be found,
        tap the magnet to the temple briefly to wake it and re-arm
        advertising, then retry.

        Args:
            timeout: Connection timeout in seconds

        Raises:
            DeviceNotFoundError: If no device found during scan
            ConnectionError: If connection fails
        """
        # Find device if no address specified
        if not self._address:
            devices = await self.scan(timeout=5.0)
            if not devices:
                raise DeviceNotFoundError(
                    "No EDGE Glasses ('Narbis_Edge') found. The glasses stop "
                    "advertising 2 minutes after the last connection - tap "
                    "the magnet to wake them, then retry."
                )
            self._address = devices[0].address

        # Connect
        try:
            self._client = BleakClient(self._address, timeout=timeout)
            await self._client.connect()
            self._connected = True
        except BleakError as e:
            raise ConnectionError(
                f"Failed to connect: {e}. If the glasses have been idle for "
                "over 2 minutes their radio is powered down - tap the magnet "
                "to wake them."
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Connection timed out after {timeout}s")

    async def disconnect(self) -> None:
        """Disconnect from glasses"""
        if self._client:
            try:
                await self._client.disconnect()
            except BleakError:
                pass  # Ignore disconnect errors
            finally:
                self._connected = False
                self._client = None

    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.disconnect()
        return False

    # -------------------------------------------------------------------------
    # Low-level Commands
    # -------------------------------------------------------------------------

    async def _write_raw(self, data: bytes) -> None:
        """
        Write raw bytes to the control characteristic (no padding)

        Args:
            data: Bytes to write

        Raises:
            ConnectionError: If not connected
            CommandError: If write fails
        """
        if not self.is_connected:
            raise ConnectionError("Not connected. Call connect() first.")

        try:
            await self._client.write_gatt_char(CHAR_UUID, data, response=True)
        except BleakError as e:
            raise CommandError(f"Command failed: {e}")

    async def _send(self, data: bytes) -> None:
        """
        Send an opcode command to the glasses

        Enforces the >=2-byte rule: a 1-byte write is interpreted by the
        firmware as the legacy opacity command, so argument-less opcodes
        are padded to [opcode, 0x00].

        Args:
            data: Command bytes (opcode + args)

        Raises:
            ConnectionError: If not connected
            CommandError: If write fails
        """
        if len(data) < 2:
            data = bytes([data[0], 0x00])
        await self._write_raw(data)

    async def send_command(self, opcode: int, payload: Optional[bytes] = None) -> None:
        """
        Send a low-level opcode command

        Pads the total write to >=2 bytes (a 1-byte write is the legacy
        opacity command). The firmware never NACKs - invalid opcodes or
        arguments are silently dropped/clamped on the device.

        Args:
            opcode: Command opcode (e.g. 0xA2)
            payload: Optional argument bytes

        Example:
            await glasses.send_command(0xA2, bytes([80]))  # brightness 80%
            await glasses.send_command(0xA7)               # sleep (padded)
        """
        opcode = max(0, min(255, int(opcode)))
        data = bytes([opcode]) + (payload or b"")
        await self._send(data)

    # -------------------------------------------------------------------------
    # Opacity (legacy single-byte write)
    # -------------------------------------------------------------------------

    async def set_opacity(self, value: int) -> None:
        """
        Set lens opacity (legacy single-byte write)

        Intentionally sends a single byte - the firmware treats any 1-byte
        write as a direct opacity command (0-255 -> 0-100% static duty).
        Stops whatever mode is currently running.

        Stream at ~12 Hz for continuous biofeedback (~20 Hz is the tolerated ceiling).

        Args:
            value: Opacity 0-255 (0=clear, 255=full dark)

        Example:
            await glasses.set_opacity(0)    # Clear
            await glasses.set_opacity(128)  # 50% dark
            await glasses.set_opacity(255)  # Full dark
        """
        value = max(0, min(255, int(value)))
        await self._write_raw(bytes([value]))

    async def clear(self) -> None:
        """Set lenses to fully clear (transparent)"""
        await self.set_opacity(0)

    async def dark(self) -> None:
        """Set lenses to fully dark (opaque)"""
        await self.set_opacity(255)

    # -------------------------------------------------------------------------
    # Parameter Commands
    # -------------------------------------------------------------------------

    async def set_brightness(self, percent: int) -> None:
        """
        Set the lens level / breathe depth (0xA2)

        Persisted in NVS across power cycles. Does not change mode.
        Writes the SAME firmware variable as set_static() -- it is not a
        ceiling that clamps later set_static() writes; a later set_static()
        simply overwrites it.

        Args:
            percent: Brightness 0-100%
        """
        percent = max(0, min(100, int(percent)))
        await self._send(bytes([0xA2, percent]))

    async def set_static(self, duty: int) -> None:
        """
        Enter static mode at a fixed duty cycle (0xA5)

        Stops the current mode and holds the lens at the given tint.
        Note: duty 1-100% maps to a perceptual floor on the device
        (raw 265-1023); 0 is fully clear.

        Args:
            duty: Duty cycle 0-100%
        """
        duty = max(0, min(100, int(duty)))
        await self._send(bytes([0xA5, duty]))

    async def set_strobe_frequency(self, hz: int) -> None:
        """
        Set strobe frequency (0xAB)

        Persisted in NVS. Takes effect immediately if strobing.

        Args:
            hz: Frequency 1-50 Hz
        """
        hz = max(1, min(50, int(hz)))
        await self._send(bytes([0xAB, hz]))

    async def set_strobe_duty(self, percent: int) -> None:
        """
        Set strobe duty cycle (0xAC)

        Persisted in NVS. Takes effect immediately if strobing.

        Args:
            percent: Dark-phase duty 10-90%
        """
        percent = max(10, min(90, int(percent)))
        await self._send(bytes([0xAC, percent]))

    async def set_duration(self, minutes: int) -> None:
        """
        Set session duration (0xA4)

        Persisted in NVS. Device auto-sleeps when the session ends.

        Args:
            minutes: Session length 1-60 minutes
        """
        minutes = max(1, min(60, int(minutes)))
        await self._send(bytes([0xA4, minutes]))

    # -------------------------------------------------------------------------
    # Mode Commands
    # -------------------------------------------------------------------------

    async def start_strobe(
        self,
        hz: Optional[int] = None,
        duty_pct: Optional[int] = None
    ) -> None:
        """
        Start strobe mode (0xA6)

        Optionally writes frequency (0xAB) and duty (0xAC) first; omitted
        parameters keep their current (NVS-persisted) values.

        Args:
            hz: Optional strobe frequency 1-50 Hz
            duty_pct: Optional dark-phase duty 10-90%

        Example:
            await glasses.start_strobe(hz=10, duty_pct=50)
            await glasses.start_strobe()  # use stored settings
        """
        if hz is not None:
            await self.set_strobe_frequency(hz)
        if duty_pct is not None:
            await self.set_strobe_duty(duty_pct)
        await self._send(bytes([0xA6, 0x00]))

    async def start_breathe(
        self,
        bpm: Optional[int] = None,
        inhale_pct: Optional[int] = None,
        hold_top_ms: Optional[int] = None,
        hold_bottom_ms: Optional[int] = None,
        waveform: Optional[Waveform] = None,
        with_strobe: bool = False
    ) -> None:
        """
        Start breathe mode (0xB0)

        Writes only the parameters you pass (0xB1-0xB5), then starts the
        on-board breathe engine. Omitted parameters keep their current
        (NVS-persisted) values. With ``with_strobe=True`` (firmware >=
        4.15.6) the strobe's dark-phase duty is modulated by the breathing
        waveform.

        Args:
            bpm: Breathing rate 1-30 BPM (integer; for fractional rates
                use sync_breath())
            inhale_pct: Inhale portion of the cycle 10-90%
            hold_top_ms: Hold at full-dark 0-5000 ms (100 ms resolution)
            hold_bottom_ms: Hold at clear 0-5000 ms (100 ms resolution)
            waveform: Waveform.SINE or Waveform.LINEAR
            with_strobe: Start breathe+strobe instead of plain breathe

        Example:
            await glasses.start_breathe(bpm=6)  # 6 BPM, device defaults
            await glasses.start_breathe(
                bpm=5, inhale_pct=40, hold_top_ms=1000,
                waveform=Waveform.SINE
            )
        """
        if bpm is not None:
            bpm = max(1, min(30, int(bpm)))
            await self._send(bytes([0xB1, bpm]))
        if inhale_pct is not None:
            inhale_pct = max(10, min(90, int(inhale_pct)))
            await self._send(bytes([0xB2, inhale_pct]))
        if hold_top_ms is not None:
            units = max(0, min(50, int(hold_top_ms) // 100))
            await self._send(bytes([0xB3, units]))
        if hold_bottom_ms is not None:
            units = max(0, min(50, int(hold_bottom_ms) // 100))
            await self._send(bytes([0xB4, units]))
        if waveform is not None:
            await self._send(bytes([0xB5, 1 if int(waveform) else 0]))
        await self._send(bytes([0xB0, 0x01 if with_strobe else 0x00]))

    async def sync_breath(self, cycle_ms: int, inhale_pct: int = 40) -> None:
        """
        Phase-lock the breathe engine to an app-paced cycle (0xBA)

        Restarts the breathe cosine at the instant of the write and sets
        the EXACT cycle length in milliseconds - this is how you get
        fractional breathing rates (the 0xB1 rate command is integer-BPM
        only). Requires firmware >= 4.15.5; older firmware ignores it,
        so it is always safe to send.

        IMPORTANT: send this only at the breath-cycle boundary (the start
        of an inhale), never mid-breath - the engine restarts its waveform
        immediately on receipt. The sync auto-expires 2 cycles after the
        last write, reverting to the stored integer-BPM rate, so re-send
        once per breath to stay locked.

        Wire format: [0xBA, cycle_ms_lo, cycle_ms_hi, inhale_pct]
        (cycle length as u16 little-endian).

        Args:
            cycle_ms: Full breath cycle length in ms (e.g. 5500 for
                10.9 BPM)
            inhale_pct: Inhale portion of the cycle 10-90% (default 40)

        Example:
            # 5.5 s cycle with 40% inhale, sent at each inhale onset
            await glasses.sync_breath(5500, inhale_pct=40)
        """
        cycle_ms = max(0, min(65535, int(cycle_ms)))
        inhale_pct = max(10, min(90, int(inhale_pct)))
        await self._send(bytes([
            0xBA,
            cycle_ms & 0xFF,
            (cycle_ms >> 8) & 0xFF,
            inhale_pct
        ]))

    # -------------------------------------------------------------------------
    # Power / Maintenance
    # -------------------------------------------------------------------------

    async def sleep(self) -> None:
        """Put glasses into deep sleep now (0xA7)"""
        await self._send(bytes([0xA7, 0x00]))

    async def factory_reset(self) -> None:
        """Reset all NVS-persisted settings to factory defaults (0xBF)"""
        await self._send(bytes([0xBF, 0x00]))

    # -------------------------------------------------------------------------
    # Preset Sessions
    # -------------------------------------------------------------------------
    # Fixed-parameter presets: the firmware no longer ramps any parameter
    # over the session, so each preset just configures the breathe/strobe
    # engine and sets the auto-sleep duration.

    async def session_relax(self, duration: int = 10) -> None:
        """
        Start a relaxation session

        5 BPM sine breathing at full brightness. Fixed parameters -
        nothing ramps over the session. Auto-sleeps when done.

        Args:
            duration: Session length in minutes
        """
        await self.set_brightness(100)
        await self.start_breathe(bpm=5, waveform=Waveform.SINE)
        await self.set_duration(duration)

    async def session_meditate(self, duration: int = 10) -> None:
        """
        Start a meditation session

        6 BPM sine breathing (the device default). Fixed parameters -
        nothing ramps over the session. Auto-sleeps when done.

        Args:
            duration: Session length in minutes
        """
        await self.start_breathe(bpm=6, waveform=Waveform.SINE)
        await self.set_duration(duration)

    async def session_focus(self, duration: int = 10) -> None:
        """
        Start a focus/concentration session

        Breathe+strobe: 12 Hz strobe modulated by 8 BPM breathing.
        Fixed parameters - nothing ramps over the session. Auto-sleeps
        when done.

        Args:
            duration: Session length in minutes
        """
        await self.set_strobe_frequency(12)
        await self.start_breathe(bpm=8, with_strobe=True)
        await self.set_duration(duration)

    async def session_sleep(self, duration: int = 15) -> None:
        """
        Start a sleep preparation session

        4 BPM sine breathing. Fixed parameters - nothing ramps over the
        session. Device auto-sleeps when the session ends.

        Args:
            duration: Session length in minutes
        """
        await self.start_breathe(bpm=4, waveform=Waveform.SINE)
        await self.set_duration(duration)

    # -------------------------------------------------------------------------
    # Real-time Feedback Streaming
    # -------------------------------------------------------------------------

    def start_feedback_stream(self, rate_hz: float = 12.0) -> "FeedbackStream":
        """
        Open a plug-and-play real-time lens stream (the screen-dimmer pattern)

        Returns a FeedbackStream: push a value from any callback at any
        rate via feed() / feed_reward(); a background task writes the lens
        at ``rate_hz`` (default ~12 Hz, the production-proven rate),
        coalescing unchanged values and keeping exactly one write in
        flight. Replaces a hand-rolled decimate/coalesce/serialize loop.

        Proportional feedback (a dimmer that tracks your signal) uses
        feed() / feed_reward(); discrete operant rewards use reward_event(),
        which fires immediately instead of waiting for the next tick.

        Usage:
            stream = glasses.start_feedback_stream()
            your_pipeline.on_update(stream.feed_reward)   # 0..1, any rate
            ...
            await stream.reward_event(hold_ms=150)        # discrete reward, now
            ...
            await stream.stop()   # cancels the writer and clears the lens

        Must be called with an asyncio event loop running (e.g. inside
        ``async with Glasses() as glasses:``).
        """
        return FeedbackStream(self, rate_hz=rate_hz)


class FeedbackStream:
    """
    Push-style real-time lens control - a wearable screen dimmer

    Created via Glasses.start_feedback_stream(). Call feed()/feed_reward()
    from anywhere (BLE notification handlers, LSL callbacks, UDP readers -
    any rate); the internal writer decimates to the stream rate, skips
    unchanged values, and never overlaps BLE writes. A failed write resets
    the coalesce key so the next tick retries.
    """

    def __init__(self, glasses: "Glasses", rate_hz: float = 12.0):
        self._glasses = glasses
        self._interval = 1.0 / max(1.0, min(20.0, rate_hz))  # 20 Hz ceiling
        self._duty: Optional[int] = None    # latest requested duty, 0-100
        self._last_sent = -1
        self._loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()         # serializes writer vs. reward_event
        self._hold_until = 0.0              # loop.time() until which a reward tint holds
        self._task = self._loop.create_task(self._run())

    def feed(self, duty: int) -> None:
        """Request a lens duty: 0 = clear ... 100 = fully dark.

        Cheap and safe to call at any rate; only changed values reach BLE.
        Use this for PROPORTIONAL feedback (a dimmer that tracks your signal).
        """
        self._duty = max(0, min(100, int(round(duty))))

    def feed_reward(self, value: float) -> None:
        """Request tint from a 0..1 reward value (1 = in condition = clear).

        The classic dimmer mapping: duty = (1 - value) * 100.
        """
        value = max(0.0, min(1.0, float(value)))
        self.feed((1.0 - value) * 100)

    async def reward_event(self, duty: int = 0, hold_ms: int = 0) -> None:
        """Deliver a DISCRETE reward NOW, bypassing the stream tick.

        For operant conditioning: call the instant your detector crosses
        threshold. Unlike feed(), which parks the value for the next
        scheduled tick (up to one stream period later), this writes
        immediately -- latency is just the BLE transport (~20-60 ms), with
        no cadence jitter. It preempts the proportional stream, waiting at
        most one in-flight write (never queues behind routine dimmer
        updates).

        Args:
            duty: reward tint 0-100 (default 0 = fully clear = positive
                reward).
            hold_ms: hold the reward tint this long before the proportional
                stream resumes (0 = let the next feed() value take back over
                immediately).
        """
        duty = max(0, min(100, int(round(duty))))
        async with self._lock:              # waits out at most one tick write
            self._last_sent = duty
            try:
                await self._glasses.set_static(duty)
            except Exception:
                self._last_sent = -1
        if hold_ms > 0:
            self._hold_until = self._loop.time() + hold_ms / 1000.0

    async def _run(self) -> None:
        while True:
            duty = self._duty
            if (duty is not None and duty != self._last_sent
                    and self._loop.time() >= self._hold_until
                    and not self._lock.locked()):   # yield to reward_event
                async with self._lock:
                    self._last_sent = duty           # claim before the await
                    try:
                        await self._glasses.set_static(duty)
                    except Exception:
                        self._last_sent = -1         # failed write: retry next tick
            await asyncio.sleep(self._interval)

    async def stop(self, clear: bool = True) -> None:
        """Stop the writer. By default clears the lens - it otherwise
        FREEZES at the last tint (see protocol doc, Reconnection)."""
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        if clear and self._glasses.is_connected:
            await self._glasses.clear()
