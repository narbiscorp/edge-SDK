"""
LSL (Lab Streaming Layer) Integration Example
The EDGE Glasses as an LSL actuator: listen for a control stream, drive the lens

LSL is the standard for real-time data streaming in neuroscience research.
This example implements the Edge LSL conventions (see docs/INTEGRATION_GUIDE.md):

- Listens for a control stream (default name 'GlassesControl', type 'Control');
  the channel labeled 'Opacity' drives the lens as percent 0-100 float
  (unit 'normalized' = 0-1, rescaled). Newest stream wins if several match.
- Emits status stream 'NarbisEdgeStatus' (type 'ListenerStatus', 3x float32,
  ~1 Hz: Opacity / Battery=NaN / ClientConnected) whose metadata announces
  what the bridge is listening for -- LSL has no sink discovery; this is it.

Requires:
    pip install edge-glasses pylsl

Compatible with:
    - OpenBCI GUI (via LSL)
    - BCI2000
    - BCILAB
    - OpenViBE
    - NeuroPype
    - Any LSL-compatible software
"""

import asyncio
import math
import time
from typing import Optional, Tuple
from edge_glasses import Glasses

try:
    from pylsl import StreamInfo, StreamOutlet, StreamInlet, resolve_stream
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    print("Warning: pylsl not installed. Run: pip install pylsl")


try:
    from edge_glasses import __version__ as BRIDGE_VERSION
except ImportError:
    BRIDGE_VERSION = "unknown"

# Control stream (what the bridge listens for)
CONTROL_STREAM_NAME = "GlassesControl"   # default; override via constructor/CLI
CONTROL_STREAM_TYPE = "Control"
CONTROL_CHANNEL_LABEL = "Opacity"        # matched case-insensitively
CONTROL_RANGE = (0.0, 100.0)             # prescribed default: percent, float

# Status stream (what the bridge emits)
STATUS_STREAM_NAME = "NarbisEdgeStatus"
STATUS_STREAM_TYPE = "ListenerStatus"    # announces the listener + its status
STATUS_RATE_HZ = 1.0

# Lens write policy: control samples are decimated to <= 12 Hz and unchanged
# values are coalesced (no redundant BLE writes).
MAX_LENS_WRITE_HZ = 12


def pick_newest_stream(streams):
    """
    Edge LSL convention: when resolve returns multiple matching streams,
    connect to the most recently created one, not streams[0].
    """
    return max(streams, key=lambda s: s.created_at())


class GlassesLSLBridge:
    """
    LSL actuator bridge for EDGE Glasses

    - Listens to a control stream and drives the lens (set_static, 0-100%)
    - Emits a ListenerStatus stream announcing the listener and its status
      (LSL has no sink discovery -- the status stream is how senders find us)
    """

    def __init__(
        self,
        control_stream_name: str = CONTROL_STREAM_NAME,
        expected_range: Optional[Tuple[float, float]] = None,
    ):
        """
        Args:
            control_stream_name: Name of the control stream to listen for
            expected_range: Optional (lo, hi) input range of the sender,
                mapped linearly onto 0-100%. Use to adapt senders with fixed
                output ranges (e.g. (0, 255)). Default: derived from the
                sender's channel <unit> metadata ('normalized' = 0-1,
                otherwise percent 0-100).
        """
        self.control_stream_name = control_stream_name
        self.expected_range = expected_range

        self.glasses: Optional[Glasses] = None
        self.outlet = None
        self.inlet = None
        self.running = False

        # State tracking
        self.control_channel = 0
        self.input_range = CONTROL_RANGE      # resolved when the inlet connects
        self.current_opacity = 0.0            # last commanded duty, percent

    async def connect_glasses(self):
        """Connect to EDGE Glasses"""
        print("Connecting to EDGE Glasses...")
        self.glasses = Glasses()
        await self.glasses.connect()
        print("  Glasses connected!")

    def create_outlet(self):
        """
        Create the status outlet: NarbisEdgeStatus, type ListenerStatus.

        Not type 'Markers' -- that type is reserved by convention for
        single-channel, string, irregular-rate streams and many tools assume
        exactly that shape. Metadata layout follows the XDF conventions
        (https://github.com/sccn/xdf/wiki/Meta-Data).
        """
        if not LSL_AVAILABLE:
            raise RuntimeError("pylsl not installed")

        info = StreamInfo(
            name=STATUS_STREAM_NAME,
            type=STATUS_STREAM_TYPE,
            channel_count=3,
            nominal_srate=STATUS_RATE_HZ,
            channel_format='float32',
            source_id='narbis_edge_bridge',
        )
        desc = info.desc()

        # <channels>: Opacity / Battery / ClientConnected
        channels = desc.append_child("channels")
        for label, unit in (
            ("Opacity", "percent"),          # last commanded lens duty
            ("Battery", "percent"),          # NaN: no battery readout over BLE
            ("ClientConnected", "binary"),   # 1.0 while BLE link is live
        ):
            ch = channels.append_child("channel")
            ch.append_child_value("label", label)
            ch.append_child_value("unit", unit)

        # <expects>: what this listener is watching the network for.
        # Reflects any user overrides of the stream name / expected range.
        expects = desc.append_child("expects")
        expects.append_child_value("stream_name", self.control_stream_name)
        expects.append_child_value("type", CONTROL_STREAM_TYPE)
        exp_channels = expects.append_child("channels")
        exp_ch = exp_channels.append_child("channel")
        exp_ch.append_child_value("label", CONTROL_CHANNEL_LABEL)
        if self.expected_range is None:
            exp_ch.append_child_value("unit", "percent")
            lo, hi = CONTROL_RANGE
        else:
            # Custom sender range, mapped linearly onto 0-100%; the unit is
            # sender-defined, so none is declared.
            exp_ch.append_child_value("unit", "")
            lo, hi = self.expected_range
        rng = exp_ch.append_child("range")
        rng.append_child_value("min", f"{lo:g}")
        rng.append_child_value("max", f"{hi:g}")

        # <acquisition>: device identity. Serial / hardware / firmware are
        # not readable over BLE (the Edge exposes no DIS), so left empty.
        acq = desc.append_child("acquisition")
        acq.append_child_value("manufacturer", "Narbis")
        acq.append_child_value("model", "Narbis Edge")
        acq.append_child_value("serial_number", "")
        acq.append_child_value("hardware_version", "")
        acq.append_child_value("firmware_version", "")
        acq.append_child_value("bridge_version", BRIDGE_VERSION)

        self.outlet = StreamOutlet(info)
        print(f"LSL status outlet created: {STATUS_STREAM_NAME} "
              f"(type {STATUS_STREAM_TYPE})")

    def find_inlet(self, timeout: float = 5.0):
        """
        Find and connect to the control stream

        Args:
            timeout: Search timeout in seconds
        """
        if not LSL_AVAILABLE:
            raise RuntimeError("pylsl not installed")

        print(f"Looking for LSL stream '{self.control_stream_name}'...")
        streams = resolve_stream('name', self.control_stream_name,
                                 timeout=timeout)

        if not streams:
            print("  No stream found (will work without external control)")
            return False

        if len(streams) > 1:
            print(f"  {len(streams)} matching streams; "
                  f"using the most recently created")
        self.inlet = StreamInlet(pick_newest_stream(streams))
        self._select_channel()
        print("  Connected to control stream!")
        return True

    def _select_channel(self):
        """
        Pick the control channel from the inlet's metadata.

        Prefer the channel labeled 'Opacity' (case-insensitive). Fallbacks:
        a single-channel stream is used regardless of label; a multi-channel
        stream with no matching label falls back to channel 0 with a warning.
        """
        info = self.inlet.info(timeout=5.0)
        n_channels = info.channel_count()

        labels = []
        units = []
        ch = info.desc().child("channels").child("channel")
        while not ch.empty():
            labels.append(ch.child_value("label"))
            units.append(ch.child_value("unit"))
            ch = ch.next_sibling("channel")
        while len(labels) < n_channels:    # sender omitted (some) metadata
            labels.append("")
            units.append("")

        self.control_channel = 0
        match = next(
            (i for i, lbl in enumerate(labels)
             if lbl.strip().lower() == CONTROL_CHANNEL_LABEL.lower()),
            None,
        )
        if match is not None:
            self.control_channel = match
        elif n_channels > 1:
            print(f"  Warning: no channel labeled '{CONTROL_CHANNEL_LABEL}' "
                  f"among {n_channels} channels; using channel 0")
        # else: single-channel stream -> use it regardless of label

        # Input range: explicit override > 'normalized' unit > percent default
        unit = units[self.control_channel].strip().lower()
        if self.expected_range is not None:
            self.input_range = (float(self.expected_range[0]),
                                float(self.expected_range[1]))
        elif unit == "normalized":
            self.input_range = (0.0, 1.0)
        else:
            self.input_range = CONTROL_RANGE

        print(f"  Control channel: {self.control_channel} "
              f"(range {self.input_range[0]:g}-{self.input_range[1]:g} "
              f"-> 0-100%)")

    def _to_duty(self, value: float) -> float:
        """Map a control value to lens duty; always clamp to 0-100."""
        lo, hi = self.input_range
        if hi != lo:
            value = (value - lo) / (hi - lo) * 100.0
        return max(0.0, min(100.0, value))

    def poll_control(self) -> Optional[float]:
        """
        Drain all queued control samples and return the newest as duty (0-100)

        Draining the queue each tick decimates the sender's rate down to the
        bridge loop rate; intermediate samples are discarded.

        Returns:
            Newest duty value (0-100) or None if no new samples
        """
        if not self.inlet:
            return None

        latest = None
        try:
            while True:
                sample, _timestamp = self.inlet.pull_sample(timeout=0.0)
                if sample is None:
                    break
                latest = sample[self.control_channel]
        except Exception:
            return None

        if latest is None:
            return None
        return self._to_duty(float(latest))

    def publish_status(self):
        """
        Push one status sample: [Opacity, Battery, ClientConnected]

        Battery is always NaN on current hardware -- the Edge exposes no
        battery readout over BLE. NaN = unavailable; the channel is kept
        for spec stability.
        """
        if not self.outlet:
            return
        connected = 1.0 if (self.glasses is not None
                            and self.glasses.is_connected) else 0.0
        self.outlet.push_sample([self.current_opacity, math.nan, connected])

    async def run_bridge(self, duration: float = 60.0):
        """
        Run the LSL bridge

        This loop:
        1. Drains incoming control samples (newest wins)
        2. Updates the lens via set_static(), coalescing unchanged values
        3. Publishes status at ~1 Hz

        Args:
            duration: How long to run (seconds)
        """
        print(f"\nRunning LSL bridge for {duration}s...")
        print(f"Announcing status on: {STATUS_STREAM_NAME}")
        if self.inlet:
            print(f"Receiving commands from: {self.control_stream_name}")
        print("-" * 40)

        self.running = True
        start_time = time.time()
        last_status = 0.0
        last_written: Optional[int] = None

        try:
            while self.running and (time.time() - start_time) < duration:
                # Newest queued control value, mapped and clamped to 0-100
                duty = self.poll_control()
                if duty is not None:
                    target = int(round(duty))
                    if target != last_written:   # coalesce unchanged values
                        await self.glasses.set_static(target)
                        last_written = target
                        self.current_opacity = float(target)
                        print(f"  Control: {duty:.1f}% -> set_static({target})")

                # Status heartbeat at ~1 Hz
                now = time.time()
                if now - last_status >= 1.0 / STATUS_RATE_HZ:
                    self.publish_status()
                    last_status = now

                # 10 Hz loop -- keeps lens writes under the <= 12 Hz cap
                await asyncio.sleep(0.1)

        finally:
            self.running = False

    async def cleanup(self):
        """Cleanup connections"""
        if self.glasses:
            await self.glasses.clear()
            await self.glasses.disconnect()
        print("Cleanup complete")


class LSLNeurofeedback:
    """
    Receive EEG data from LSL and control glasses

    Works with any LSL EEG source:
    - OpenBCI GUI (enable LSL output)
    - Muse via muse-lsl
    - Any LSL-compatible EEG device
    """

    def __init__(self):
        self.glasses: Optional[Glasses] = None
        self.inlet = None
        self.running = False

        # Processing parameters
        self.alpha_band = (8, 12)  # Hz
        self.baseline_alpha = 1.0
        self.alpha_history = []
        self.history_len = 10

    async def connect(self):
        """Connect to glasses and find EEG stream"""
        print("Connecting to EDGE Glasses...")
        self.glasses = Glasses()
        await self.glasses.connect()

        if not LSL_AVAILABLE:
            raise RuntimeError("pylsl not installed")

        print("Looking for EEG stream...")
        streams = resolve_stream('type', 'EEG', timeout=10.0)

        if not streams:
            raise RuntimeError("No EEG stream found!")

        # Most recently created stream wins when several match
        self.inlet = StreamInlet(pick_newest_stream(streams))
        info = self.inlet.info()
        print(f"  Connected to: {info.name()} ({info.channel_count()} channels, {info.nominal_srate()} Hz)")

    def calculate_alpha(self, sample: list) -> float:
        """
        Calculate alpha power from EEG sample
        Simplified - real implementation would use FFT
        """
        # This is a placeholder - real implementation needs proper spectral analysis
        # For actual use, process with scipy.signal or mne
        import numpy as np
        return np.abs(np.mean(sample)) / 100.0  # Normalized placeholder

    def smooth(self, value: float) -> float:
        """Apply temporal smoothing"""
        self.alpha_history.append(value)
        if len(self.alpha_history) > self.history_len:
            self.alpha_history.pop(0)
        import numpy as np
        return float(np.mean(self.alpha_history))

    async def run(self, duration: float = 60.0):
        """
        Run neurofeedback loop

        Higher alpha = darker glasses (relaxed state)
        """
        print(f"\nRunning LSL neurofeedback for {duration}s...")
        print("Higher alpha power = darker glasses")
        print("-" * 40)

        self.running = True
        start_time = time.time()

        try:
            while self.running and (time.time() - start_time) < duration:
                # Get EEG sample
                sample, timestamp = self.inlet.pull_sample(timeout=0.1)

                if sample:
                    # Calculate alpha power
                    alpha = self.calculate_alpha(sample)
                    smoothed = self.smooth(alpha)

                    # Normalize and send to glasses
                    normalized = min(1.0, smoothed / self.baseline_alpha)
                    opacity = int(normalized * 255)

                    await self.glasses.set_opacity(opacity)

                    # Status update
                    elapsed = time.time() - start_time
                    if int(elapsed) % 5 == 0:  # Every 5 seconds
                        print(f"  Alpha: {smoothed:.3f} -> Opacity: {opacity}")

                await asyncio.sleep(0.05)  # 20 Hz — max recommended rate for streaming set_opacity

        finally:
            self.running = False
            await self.glasses.clear()

    async def cleanup(self):
        """Cleanup"""
        if self.glasses:
            await self.glasses.disconnect()


async def demo_bridge(control_stream_name: str = CONTROL_STREAM_NAME,
                      expected_range: Optional[Tuple[float, float]] = None):
    """Demo: LSL actuator bridge (listen for control, announce status)"""
    bridge = GlassesLSLBridge(
        control_stream_name=control_stream_name,
        expected_range=expected_range,
    )

    try:
        await bridge.connect_glasses()
        bridge.create_outlet()
        bridge.find_inlet(timeout=2.0)  # Optional control inlet

        await bridge.run_bridge(duration=60.0)

    except KeyboardInterrupt:
        print("\nStopped!")
    finally:
        await bridge.cleanup()


async def demo_neurofeedback():
    """Demo: EEG neurofeedback via LSL"""
    nf = LSLNeurofeedback()

    try:
        await nf.connect()
        await nf.run(duration=60.0)

    except KeyboardInterrupt:
        print("\nStopped!")
    finally:
        await nf.cleanup()


def parse_args():
    import argparse
    parser = argparse.ArgumentParser(
        description="EDGE Glasses - LSL integration demos")
    parser.add_argument(
        "--control-stream", default=CONTROL_STREAM_NAME,
        help="control stream name to listen for (default: %(default)s)")
    parser.add_argument(
        "--expected-range", nargs=2, type=float, metavar=("LO", "HI"),
        default=None,
        help="sender output range mapped onto 0-100%% (e.g. 0 255); "
             "default: from the sender's <unit> metadata")
    return parser.parse_args()


async def main():
    args = parse_args()

    print("EDGE Glasses - LSL Integration")
    print("=" * 40)
    print()
    print("1. LSL Bridge (listen for control, announce status)")
    print("2. EEG Neurofeedback (receive EEG, control glasses)")
    print()

    choice = input("Select (1-2): ").strip()

    if choice == "1":
        expected_range = tuple(args.expected_range) if args.expected_range else None
        await demo_bridge(args.control_stream, expected_range)
    elif choice == "2":
        await demo_neurofeedback()
    else:
        print("Invalid choice")


if __name__ == "__main__":
    asyncio.run(main())
