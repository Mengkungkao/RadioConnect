"""Push-to-talk recording, with a pre-roll so the first word survives.

The naive design -- spawn `arecord` when the button passes the hold
threshold -- loses the beginning of every transmission. Measured on both
Pis, the WM8960 needs **~690 ms** to power up its ADC and deliver a
first sample, and that cost is paid on every cold open regardless of
rate, channel count, or whether ALSA's plug layer is involved:

    plughw 8000 mono      702 ms      hw 48000 stereo       686 ms
    plughw 48000 stereo   678 ms      fork+exec alone         9 ms

On top of the 350 ms hold threshold, that is over a second of dead air.
Presses shorter than that captured *nothing*, which surfaced in the logs
as `recorded 0.00s (0 B PCM)` and, to the operator, as a radio that
silently refused to send.

The same measurement shows the way out: opening a second capture while
one is already running takes **15 ms**. The delay is the codec powering
up, not the open itself. So the recorder keeps one `arecord` running
while armed, feeding a small ring buffer, and PTT simply marks a
position in it. Latency becomes negligible, and the ring means the
audio from *before* the hold threshold is kept too -- so a user who
starts talking as they press still gets their first syllable.

Power is the reason this is armed rather than permanent. A warm codec
costs current continuously, which is exactly what a belt-worn radio
cannot afford. Arming follows the screen: awake means in use and PTT is
instant, and when the backlight blanks the capture stream is dropped and
the codec powers down. `start()` still works when disarmed, falling back
to a cold spawn, so nothing depends on the arming being right.

The capture card is exclusive: while another program holds it, `arecord`
quits at once with "Device or resource busy". On an Orange Pi Zero 2W
(2026-10-22) a recorder the AI Chatbot left behind did exactly that, and
every press recorded 0 bytes and said only "too short". So a stream that
ends by itself keeps arecord's reason in `error`, `failed` tells the app
while the press is still held, and the next press opens the card again
instead of trusting a dead stream.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque

from app.audio import dsp
from app.utils.logger import get_logger

log = get_logger("capture")

# The card is opened at its own rate, in stereo, and the audio converted
# here: a proper filter for the rate, and both channels mixed to one (asked
# for a single channel, the Whisplay card on an Orange Pi Zero 2W gave the
# microphone 35 dB down). See app.audio.dsp for both.
CAPTURE_RATE = dsp.HARDWARE_RATE
CAPTURE_CHANNELS = 2
FRAME_BYTES = 2 * CAPTURE_CHANNELS   # one 16-bit sample per channel
BYTES_PER_SECOND = CAPTURE_RATE * 2  # 16-bit mono, after the mix
CHUNK = 12288                        # ~64 ms of stereo per pipe read

# How much audio from before the hold threshold to keep. The threshold is
# 350 ms, so this covers a user who speaks the instant they press.
PREROLL_SECONDS = 0.5

try:
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None


def _rms(pcm: bytes) -> float:
    """Normalised 0..1 loudness of a PCM chunk, for the VU meter."""
    if not pcm or len(pcm) < 2:
        return 0.0
    if _np is not None:
        samples = _np.frombuffer(pcm[: len(pcm) & ~1], dtype="<i2").astype("f4")
        if not samples.size:
            return 0.0
        return float(min(1.0, (_np.sqrt((samples ** 2).mean()) / 32768.0) * 4.0))
    total = 0
    count = len(pcm) // 2
    for index in range(0, count * 2, 2):
        value = int.from_bytes(pcm[index:index + 2], "little", signed=True)
        total += value * value
    return min(1.0, ((total / count) ** 0.5 / 32768.0) * 4.0)


# More than this share of samples at full scale is audible distortion.
CLIPPED_TOO_MUCH = 0.001


def _peak_and_clipping(pcm: bytes) -> tuple:
    """(peak in dBFS, share of samples within 1% of full scale)."""
    if _np is None or len(pcm) < 2:
        return -120.0, 0.0
    samples = _np.abs(_np.frombuffer(pcm[: len(pcm) & ~1], dtype="<i2").astype("i4"))
    peak = int(samples.max()) if samples.size else 0
    clipped = float((samples >= 32440).mean()) if samples.size else 0.0
    return (20 * _np.log10(peak / 32768) if peak else -120.0), clipped


class Recorder:
    """Captures 8 kHz mono PCM, with the codec kept warm while armed."""

    def __init__(self, device: str | None, max_seconds: float = 30.0,
                 preroll_seconds: float = PREROLL_SECONDS):
        self.device = device
        self.max_seconds = max_seconds
        self.preroll_bytes = int(preroll_seconds * BYTES_PER_SECOND)
        self.level = 0.0
        self.started_at = 0.0
        # Of the last recording: the share of samples at full scale. Clipped
        # audio is distorted before anything here can help, and levelling
        # afterwards only makes the distortion louder.
        self.last_clipped = 0.0
        # Why the capture stream ended without being asked, in arecord's own
        # words (another program holding the card: "... Device or resource
        # busy"); None while it runs or after a clean stop.
        self.error = None

        self._process = None
        self._errors = None         # arecord's stderr, read once it has quit
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ring = deque()        # recent audio, bounded to the pre-roll
        self._ring_bytes = 0
        self._captured = []         # audio kept since start()
        self._captured_bytes = 0
        self._recording = False
        self._armed = False

    # --- state ---------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.device) and shutil.which("arecord") is not None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def armed(self) -> bool:
        return self._armed and self._process is not None

    @property
    def failed(self) -> bool:
        """A press is held, but the stream feeding it has ended."""
        return self._recording and self.error is not None

    @property
    def busy(self) -> bool:
        """The last stream ended because another program has the card."""
        return bool(self.error) and "busy" in self.error.lower()

    def _live(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self.started_at else 0.0

    # --- arming --------------------------------------------------------
    def arm(self) -> bool:
        """Keep the codec powered so the next PTT starts instantly."""
        if not self.available or self._process is not None:
            return self._process is not None
        if not self._spawn():
            return False
        self._armed = True
        log.info("capture armed (codec warm, pre-roll %.0f ms)",
                 self.preroll_bytes / BYTES_PER_SECOND * 1000)
        return True

    def disarm(self):
        """Release the capture device so the codec can power down."""
        if self._recording or self._process is None:
            return
        self._armed = False
        self._teardown()
        log.info("capture disarmed (codec powering down)")

    # --- process plumbing ----------------------------------------------
    def _spawn(self) -> bool:
        command = [
            "arecord", "-q", "-D", self.device, "-t", "raw",
            "-f", "S16_LE", "-r", str(CAPTURE_RATE), "-c", str(CAPTURE_CHANNELS),
        ]
        # A file, not a pipe: nobody reads it while arecord runs, and a full
        # pipe would stall the audio behind it.
        errors = tempfile.TemporaryFile()
        try:
            self._process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=errors, bufsize=0,
            )
        except OSError:
            log.exception("could not start arecord")
            errors.close()
            self._process = None
            return False

        self._errors = errors
        self.error = None
        self._stop.clear()
        with self._lock:
            self._ring.clear()
            self._ring_bytes = 0
        self._thread = threading.Thread(target=self._drain, name="capture",
                                        args=(self._process, errors), daemon=True)
        self._thread.start()
        return True

    def _teardown(self):
        self._stop.set()
        process, self._process = self._process, None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._errors is not None:
            self._errors.close()
            self._errors = None
        self.level = 0.0

    def _drain(self, process, errors):
        stream = process.stdout if process else None
        if stream is None:
            return
        limit = int(self.max_seconds * BYTES_PER_SECOND)
        capped = False
        partial = b""               # a pipe read can end inside a frame
        while not self._stop.is_set():
            try:
                data = stream.read(CHUNK)
            except (ValueError, OSError):
                break
            if not data:
                break
            data = partial + data
            whole = len(data) - len(data) % FRAME_BYTES
            data, partial = dsp.to_mono(data[:whole]), data[whole:]
            if not data:
                continue
            self.level = _rms(data)
            with self._lock:
                if self._recording:
                    # Past the cap the audio is dropped, but the stream is
                    # still read: once it stopped here, an armed recorder
                    # stayed "warm" with nothing feeding it, and every later
                    # press came back empty.
                    if self._captured_bytes < limit:
                        self._captured.append(data)
                        self._captured_bytes += len(data)
                    elif not capped:
                        capped = True
                        log.info("recording hit the %.0fs cap", self.max_seconds)
                else:
                    capped = False
                    self._ring.append(data)
                    self._ring_bytes += len(data)
                    while self._ring_bytes - len(self._ring[0]) >= self.preroll_bytes:
                        self._ring_bytes -= len(self._ring.popleft())
        self.level = 0.0
        if not self._stop.is_set():
            self._ended_by_itself(process, errors)

    def _ended_by_itself(self, process, errors):
        """arecord quit without being asked: keep its reason for the app."""
        try:
            code = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            code = None
        said = ""
        try:
            errors.seek(0)
            lines = errors.read()[-512:].decode("utf-8", "replace").splitlines()
            said = next((line.strip() for line in reversed(lines) if line.strip()), "")
        except (ValueError, OSError):
            pass
        self.error = said or f"arecord exited with code {code}"
        log.warning("the microphone stream ended by itself: %s%s", self.error,
                    " -- another program is holding the sound card" if self.busy else "")

    # --- push to talk ---------------------------------------------------
    def start(self) -> bool:
        """Begin keeping audio. Instant when armed; cold-starts otherwise."""
        if self._recording or not self.available:
            return False

        if self._process is not None and not self._live():
            # The stream ended under us (arecord quit, e.g. the card was
            # busy): a "warm" start would keep nothing. Open it again.
            self._teardown()
        cold = self._process is None
        if cold and not self._spawn():
            return False

        with self._lock:
            # Carry the ring across: the operator may already be speaking.
            self._captured = list(self._ring)
            self._captured_bytes = self._ring_bytes
            self._ring.clear()
            self._ring_bytes = 0
            self._recording = True
        self.started_at = time.monotonic()
        if cold:
            log.info("capture was cold: losing the first ~700 ms to codec power-up")
        return True

    def stop(self) -> bytes:
        """Stop keeping audio and return it as 8 kHz speech, ready to encode."""
        if not self._recording:
            return b""
        with self._lock:
            self._recording = False
            pcm = b"".join(self._captured)
            self._captured = []
            self._captured_bytes = 0
        self.started_at = 0.0

        # Stay warm if armed, so the next press is instant too.
        if not self._armed:
            self._teardown()

        preroll = min(self.preroll_bytes, len(pcm))
        peak_db, self.last_clipped = _peak_and_clipping(pcm)
        log.info("recorded %.2fs (%d B PCM, %.0f ms of it pre-roll), peak %.1f dBFS, "
                 "%.2f%% clipped", len(pcm) / BYTES_PER_SECOND, len(pcm),
                 preroll / BYTES_PER_SECOND * 1000, peak_db, self.last_clipped * 100)
        if self.last_clipped > CLIPPED_TOO_MUCH:
            log.warning("the microphone clipped on %.1f%% of that recording: it is "
                        "overdriven, and will sound distorted however it is sent. "
                        "Lower audio.mic_level, or speak further from the radio.",
                        self.last_clipped * 100)
        return dsp.prepare_speech(dsp.downsample(pcm))

    def cancel(self):
        self.stop()

    def close(self):
        self._armed = False
        self._recording = False
        self._teardown()
