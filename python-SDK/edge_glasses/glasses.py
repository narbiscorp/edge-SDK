"""
EDGE Glasses - Main SDK module

Targets glasses firmware 4.15.6+ (device name ``Narbis_Edge``).
The lens-config methods (set_lens_smoothing / set_lens_max_rate /
set_disconnect_behavior) need firmware 4.15.7+; older firmware ignores
them, so they are always safe to call.

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

# Standard BLE Battery Service (firmware >= 4.16.1 on V1.2+ hardware)
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


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
        self._ctrl_supports_wnr = False  # 0xFF01 write-without-response (fw >= 4.16.3)

    @property
    def is_connected(self) -> bool:
        """Check if currently connected"""
        return self._connected and self._client is not None

    @property
    def supports_fast_write(self) -> bool:
        """True if the control characteristic advertises write-without-response.

        Firmware >= 4.16.3 exposes write-without-response on 0xFF01, letting
        the real-time streaming path (FeedbackStream / _stream_static) skip the
        ATT round-trip per write for higher sustained throughput. Older
        firmware is write-with-response only, and this stays False.
        """
        return self._ctrl_supports_wnr

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
            # Detect write-without-response on the control char (fw >= 4.16.3).
            # When present, the streaming path skips per-write ATT acks.
            self._ctrl_supports_wnr = False
            try:
                ctrl = self._client.services.get_characteristic(CHAR_UUID)
                if ctrl is not None and "write-without-response" in ctrl.properties:
                    self._ctrl_supports_wnr = True
            except Exception:
                pass
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

    async def _stream_static(self, duty: int) -> None:
        """Fast static-duty write for the real-time streaming path (0xA5).

        Uses write-without-response when the control characteristic advertises
        it (fw >= 4.16.3), which lifts sustained throughput past the ~20/sec
        that per-write acks allow; otherwise falls back to write-with-response.
        Used by FeedbackStream. Command writes (set_static, set_duration, …)
        keep write-with-response for ordering/back-pressure.
        """
        if not self.is_connected:
            raise ConnectionError("Not connected. Call connect() first.")
        duty = max(0, min(100, int(duty)))
        try:
            await self._client.write_gatt_char(
                CHAR_UUID, bytes([0xA5, duty]),
                response=not self._ctrl_supports_wnr,
            )
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

        Stream this for continuous real-time feedback. There is no 20 Hz
        protocol ceiling (that figure was a stale conservative doc value);
        target a configurable 30-50 Hz band with coalescing on. The BLE link
        paces you - write-with-response keeps exactly one write in flight, so
        the effective rate self-limits to your data rate (measured ~8-11
        writes/sec on default connection parameters, ~20/sec with
        throughput-optimized params). The old "12 Hz" was only the on-board
        breathe-pacer cadence, not a lens-control limit.

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
        Set the persistent max-tint / breathe depth (0xA2)

        Persisted in NVS across power cycles. Does not change mode. This is
        the master tint level that MULTIPLIES the breathe / strobe / static
        output.

        On firmware >= 4.16.2 this is decoupled from set_static(): 0xA2 owns
        brightness alone, and set_static() (0xA5) is a clean static-duty
        write that does NOT touch it - so you can stream real-time dimming to
        0 without zeroing the depth of the other programs. On firmware <=
        4.16.1 the two shared one variable (a set_static() to 0 left
        brightness at 0, so later breathe/strobe rendered clear until 0xA2
        was re-sent); 4.16.2 fixes this and self-heals a persisted brightness
        of 0 at boot.

        Args:
            percent: Brightness 0-100%
        """
        percent = max(0, min(100, int(percent)))
        await self._send(bytes([0xA2, percent]))

    async def set_static(self, duty: int) -> None:
        """
        Enter static mode at a fixed duty cycle (0xA5)

        Stops the current mode and holds the lens at the given tint - the
        primary real-time dimming command. On firmware >= 4.16.2 this is a
        clean static-duty write that does NOT touch the 0xA2 brightness /
        breathe depth (see set_brightness()).

        Note: duty 1-100% maps to a perceptual floor on the device
        (raw 265-1023); 0 is fully clear.

        For a smooth timed fade to a target, pair with set_lens_max_rate()
        (an on-device slew ramp) or set_lens_smoothing() (an EMA glide
        between writes) - see those methods.

        Args:
            duty: Duty cycle 0-100%
        """
        duty = max(0, min(100, int(duty)))
        await self._send(bytes([0xA5, duty]))

    async def set_strobe_frequency(self, hz: float) -> None:
        """
        Set strobe frequency (0xAB)

        Persisted in NVS. Takes effect immediately if strobing.

        Accepts a float. A whole number of Hz is sent as the 2-byte integer
        form, which every firmware understands; a fractional rate is sent as
        the 3-byte deci-Hz form, which needs **fw >= 4.14.41**. Older firmware
        reads only the first argument byte of a 3-byte frame, so a fractional
        request there would land on a wildly wrong rate - hence the split,
        rather than always sending deci-Hz.

        Sub-Hz precision is what entrainment targets need: 13.5 Hz (the
        alpha-beta edge) and 17.5 Hz both round to the wrong band as integers.

        NOTE: writing this while a strobe is running restarts the strobe
        phase, exactly as set_strobe_duty() does - the firmware recomputes the
        cycle. That is fine for setup; do not drive it from a live signal.
        See docs/strobe-feedback.md.

        Args:
            hz: Frequency 0.5-50 Hz (0.1 Hz resolution on fw >= 4.14.41)
        """
        hz = max(0.5, min(50.0, float(hz)))
        dhz = int(round(hz * 10))
        if dhz % 10 == 0:
            await self._send(bytes([0xAB, dhz // 10]))          # 2-byte form
        else:
            await self._send(bytes([0xAB, dhz & 0xFF, dhz >> 8]))  # deci-Hz

    async def set_strobe_duty(self, percent: int) -> None:
        """
        Set strobe duty cycle (0xAC)

        Persisted in NVS. Takes effect immediately if strobing.

        WARNING - do not drive this from a live feedback signal. The handler
        calls strobe_update() -> strobe_start(), which zeroes the DDS phase
        accumulator, so every write RESTARTS the strobe cycle and the wearer
        sees a brief extra dark pulse. The cost is the write itself, not the
        size of the change, so rate-limiting does not smooth it - and it hits
        NVS every time as well. To carry a signal on a running strobe, drive
        set_brightness() (0xA2) instead: see docs/strobe-feedback.md.

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
    # Lens Config (firmware >= 4.15.7; older firmware ignores these)
    # -------------------------------------------------------------------------

    async def set_lens_smoothing(self, ms: int) -> None:
        """
        Set on-device lens smoothing (0xA0)

        Persisted in NVS. The firmware glides between commanded static
        targets (set_static / set_opacity / the disconnect fail-clear)
        with an EMA of this time constant, so the lens moves continuously
        between your writes instead of stepping - the RECOMMENDED way to get
        smooth real-time feedback and to absorb per-sample noise without
        filtering client-side. Send it once at connect.
        Rule of thumb: tau ~= 1-2x your write period (a 30 Hz stream is a
        ~33 ms period -> ~35-70 ms; ~80 ms is a good general value). Affects
        commanded static duty only, not breathe/strobe waveforms.

        For a CONTINUOUS stream, use firmware >= 4.15.9: 4.15.7 stalls
        ~2-4% short of each target (fixed in 4.15.8), and through 4.15.8
        the smoothed output was still floored to ~101 duty levels
        (visible 1%-duty stepping); 4.15.9 drives the lens at full
        10-bit PWM resolution. One-shot writes are fine on any.

        Args:
            ms: Time constant 0-2550 ms, 10 ms resolution. 0 = off (snap).

        Example:
            await glasses.set_lens_smoothing(100)  # 100 ms glide
            await glasses.set_lens_smoothing(0)    # snap (factory default)
        """
        tau = max(0, min(255, int(ms) // 10))
        await self._send(bytes([0xA0, tau]))

    async def set_lens_max_rate(self, percent_per_100ms: int) -> None:
        """
        Cap how fast the lens may transition (0xA1)

        Persisted in NVS. A hard slew limit on commanded static
        transitions, applied after the smoothing glide - a safety
        envelope that guarantees the lens cannot snap even if a host
        streams garbage. 40 corresponds to full-scale in ~250 ms (the
        breathe engine's own internal limit). Does not affect
        breathe/strobe waveforms.

        Args:
            percent_per_100ms: Max change 0-100 %/100ms. 0 = unlimited
                (factory default).
        """
        rate = max(0, min(100, int(percent_per_100ms)))
        await self._send(bytes([0xA1, rate]))

    async def set_disconnect_behavior(self, fail_clear: bool) -> None:
        """
        Choose what the lens does when the BLE link drops (0xA3)

        Persisted in NVS. Factory default (False): the lens FREEZES at
        its last commanded output across a disconnect - a crashed app
        leaves the last tint in place. With fail_clear=True the glasses
        instead stop any strobe and drop to a clear static lens on link
        loss (riding the set_lens_smoothing glide if configured).

        The failsafe fires when the firmware declares the link dead,
        bounded by the ~32 s supervision timeout - still send an
        explicit clear() before an intentional disconnect.

        Args:
            fail_clear: True = go clear on disconnect, False = continue
                the running program (factory default).
        """
        await self._send(bytes([0xA3, 0x01 if fail_clear else 0x00]))

    # -------------------------------------------------------------------------
    # Mode Commands
    # -------------------------------------------------------------------------

    async def start_strobe(
        self,
        hz: Optional[float] = None,
        duty_pct: Optional[int] = None
    ) -> None:
        """
        Start strobe mode (0xA6)

        Optionally writes frequency (0xAB) and duty (0xAC) first; omitted
        parameters keep their current (NVS-persisted) values.

        Both are written BEFORE the mode is entered, so the first flash the
        wearer sees is the one that was asked for rather than whatever the
        last client left in NVS.

        To modulate a running strobe from a live signal, drive
        set_brightness() (0xA2) and nothing else - see
        docs/strobe-feedback.md, which also covers the client-side smoothing
        this needs (0xA0 glide does NOT apply to strobe).

        Args:
            hz: Optional strobe frequency 0.5-50 Hz (0.1 Hz on fw >= 4.14.41)
            duty_pct: Optional dark-phase duty 10-90%

        Example:
            await glasses.start_strobe(hz=13.5, duty_pct=50)
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
    # Battery (firmware >= 4.16.1 on V1.2+ hardware)
    # -------------------------------------------------------------------------

    async def get_battery(self) -> Optional[int]:
        """
        Read the battery charge level over the standard BLE Battery Service

        Reads Battery Level (``0x2A19``) from the standard Battery Service
        (``0x180F``), which firmware >= 4.16.1 exposes on V1.2+ hardware.

        Returns:
            Battery charge 0-100 (percent), or ``None`` if the device does
            not expose the ``0x180F`` Battery Service (pre-4.16.1 firmware,
            or a board built without the battery divider).

        Note:
            On firmware >= 4.16.1 the ``0x2A19`` characteristic is registered
            on every unit but **reads 0 while the level is unknown** - it
            cannot distinguish "unknown" from a genuine 0%. If you need to
            tell those apart (or want millivolts / a charging flag), read the
            ``0xFB`` status frame on ``0xFF03`` instead:
            ``[mv:u16 LE][soc:u8][charging:u8]``, where ``soc = 0xFF`` means
            unknown/unsupported (see the protocol doc). A board without the
            divider reports unsupported there (mv=0, soc=0xFF, charging=0xFF),
            so treat a missing ``0x180F`` service or a persistent 0 as
            "battery not available on this unit."

        Example:
            level = await glasses.get_battery()
            if level is None:
                print("battery not available on this unit")
            else:
                print(f"battery: {level}%")
        """
        if not self.is_connected:
            raise ConnectionError("Not connected. Call connect() first.")

        service = self._client.services.get_service(BATTERY_SERVICE_UUID)
        if service is None:
            return None    # pre-4.16.1 firmware / no battery service

        try:
            data = await self._client.read_gatt_char(BATTERY_LEVEL_UUID)
        except BleakError as e:
            raise CommandError(f"Battery read failed: {e}")

        if not data:
            return None
        return max(0, min(100, data[0]))

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

    def start_feedback_stream(self, rate_hz: float = 30.0) -> "FeedbackStream":
        """
        Open a plug-and-play real-time lens stream (the screen-dimmer pattern)

        Returns a FeedbackStream: push a value from any callback at any
        rate via feed() / feed_reward(); a background task writes the lens
        at ``rate_hz`` (default ~30 Hz real-time target, capped at 45 Hz),
        coalescing unchanged values and keeping exactly one write in
        flight. Replaces a hand-rolled decimate/coalesce/serialize loop.
        The rate is a target, never a queue: write-with-response keeps one
        write in flight, so the effective rate self-limits to your data rate
        (the BLE link, not the firmware, is the throughput limit).

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

    def __init__(self, glasses: "Glasses", rate_hz: float = 30.0):
        self._glasses = glasses
        self._interval = 1.0 / max(1.0, min(45.0, rate_hz))  # 45 Hz cap
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
                await self._glasses._stream_static(duty)   # fast path (WNR on 4.16.3+)
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
                        # Fast path: write-without-response on fw >= 4.16.3,
                        # else with-response. Higher sustained throughput.
                        await self._glasses._stream_static(duty)
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
