"""Push-to-talk capture -- and the lost first word that prompted it.

Spawning `arecord` at the hold threshold lost the start of every
transmission: the WM8960 needs ~690 ms to power its ADC on a cold open,
on top of the 350 ms hold threshold. Presses shorter than that captured
nothing at all, which the logs recorded as `recorded 0.00s (0 B PCM)`.

These tests drive a fake `arecord` so the behaviour can be pinned
without a codec: that PTT keeps audio from before the press, that a very
short press still yields something once armed, and that arming and
disarming actually open and close the device.
"""

from __future__ import annotations

import struct
import subprocess
import time

import pytest

from app.audio import capture
from app.audio.capture import BYTES_PER_SECOND, Recorder


class FakeArecord:
    """Stands in for the arecord process, emitting a steady byte stream."""

    instances = []

    def __init__(self, *_args, **_kwargs):
        self.terminated = False
        self._closed = False
        FakeArecord.instances.append(self)
        self.stdout = self

    # -- the pipe --
    def read(self, size):
        if self._closed:
            return b""
        time.sleep(0.01)
        # A ramp, so tests can tell one chunk from another.
        return struct.pack("<h", 4000) * (size // 2)

    # -- the process --
    def terminate(self):
        self.terminated = True
        self._closed = True

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def recorder(monkeypatch):
    FakeArecord.instances = []
    monkeypatch.setattr(subprocess, "Popen", FakeArecord)
    monkeypatch.setattr(capture.shutil, "which", lambda _name: "/usr/bin/arecord")
    rec = Recorder("plughw:1", max_seconds=5.0, preroll_seconds=0.5)
    yield rec
    rec.close()


def test_arming_opens_the_device_once(recorder):
    assert recorder.arm() is True
    assert recorder.armed
    assert len(FakeArecord.instances) == 1
    recorder.arm()  # idempotent
    assert len(FakeArecord.instances) == 1


def test_disarming_releases_the_device(recorder):
    recorder.arm()
    recorder.disarm()
    assert not recorder.armed
    assert FakeArecord.instances[0].terminated


def test_a_short_press_still_captures_audio_when_armed(recorder):
    """The regression: 'recorded 0.00s (0 B PCM)' on a real press."""
    recorder.arm()
    time.sleep(0.4)          # the codec has been warm for a while
    assert recorder.start() is True
    time.sleep(0.15)         # a brief press
    pcm = recorder.stop()
    assert len(pcm) > 0, "a short press must not come back empty"


def test_preroll_keeps_audio_from_before_the_press(recorder):
    """A user who talks as they press must keep their first syllable."""
    recorder.arm()
    time.sleep(0.6)          # fill the ring
    recorder.start()
    pcm = recorder.stop()
    # Everything captured here predates start(), so it can only be pre-roll.
    assert len(pcm) > 0
    assert len(pcm) <= recorder.preroll_bytes + BYTES_PER_SECOND


def test_the_ring_is_bounded_while_armed(recorder):
    """Idling armed for minutes must not grow memory without limit."""
    recorder.arm()
    time.sleep(1.2)          # well past the 0.5 s pre-roll
    assert recorder._ring_bytes <= recorder.preroll_bytes + capture.CHUNK


def test_recording_survives_being_started_cold(recorder):
    """Disarmed PTT must still work, just with the codec power-up cost."""
    assert not recorder.armed
    assert recorder.start() is True
    time.sleep(0.1)
    assert len(recorder.stop()) > 0


def test_staying_armed_across_a_transmission(recorder):
    """The device is not reopened between presses, so the second is instant."""
    recorder.arm()
    recorder.start(); time.sleep(0.05); recorder.stop()
    recorder.start(); time.sleep(0.05); recorder.stop()
    assert len(FakeArecord.instances) == 1
    assert recorder.armed


def test_a_cold_recording_closes_the_device_afterwards(recorder):
    """Unarmed capture must not leave the codec powered."""
    recorder.start(); time.sleep(0.05); recorder.stop()
    assert FakeArecord.instances[0].terminated
    assert not recorder.armed


def test_no_audio_device_means_no_crash():
    rec = Recorder(None)
    assert rec.available is False
    assert rec.arm() is False
    assert rec.start() is False
    assert rec.stop() == b""


def test_the_card_is_opened_at_48k_and_speech_comes_back_at_8k(recorder, monkeypatch):
    """The card's own rate in, Codec2's rate out: converted here, filtered."""
    from app.audio import dsp

    commands = []
    real = FakeArecord.__init__

    def spy(self, command, *args, **kwargs):
        commands.append(command)
        real(self, command, *args, **kwargs)

    monkeypatch.setattr(FakeArecord, "__init__", spy)
    recorder.arm()
    recorder.start()
    time.sleep(0.2)
    kept = recorder._captured_bytes
    pcm = recorder.stop()
    assert commands[0][commands[0].index("-r") + 1] == str(dsp.HARDWARE_RATE)
    assert len(pcm) == (kept // 2 // dsp.FACTOR) * 2


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class OneSidedCard(FakeArecord):
    """The Whisplay card as measured on an Orange Pi Zero 2W (2026-10-22):
    in stereo the microphone is on the left and the right is silent; asked
    for one channel, it gives the microphone 35 dB down."""

    VOICE = 16000                                  # about -6 dBFS
    FAINT = round(VOICE * 10 ** (-35 / 20))

    def __init__(self, command, *args, **kwargs):
        super().__init__(command, *args, **kwargs)
        self.channels = int(command[command.index("-c") + 1])

    def read(self, size):
        if self._closed:
            return b""
        time.sleep(0.01)
        if self.channels == 2:
            return struct.pack("<hh", self.VOICE, 0) * (size // 4)
        return struct.pack("<h", self.FAINT) * (size // 2)


def test_the_microphone_on_one_side_of_a_stereo_card_is_heard(monkeypatch):
    """The regression: RadioConnect on opi2w sent near silence ('peak -35 dBFS')
    while the AI Chatbot, recording in stereo, heard the same microphone."""
    from app.audio.capture import _peak_and_clipping

    monkeypatch.setattr(subprocess, "Popen", OneSidedCard)
    monkeypatch.setattr(capture.shutil, "which", lambda _name: "/usr/bin/arecord")
    rec = Recorder("plughw:3", max_seconds=5.0)
    try:
        rec.start()
        assert wait_for(lambda: rec._captured_bytes >= BYTES_PER_SECOND // 10)
        peak, _clipped = _peak_and_clipping(b"".join(rec._captured))
        # -6 dBFS in, mixed with a silent side: -12. One channel gave -41.
        assert peak > -15, f"the voice came back at {peak:.0f} dBFS"
    finally:
        rec.close()


def test_a_read_that_ends_inside_a_frame_keeps_the_channels_apart(monkeypatch):
    """Pipe reads need not end on a frame: the rest waits for the next read."""

    class OddReads(FakeArecord):
        def read(self, size):
            if self._closed:
                return b""
            time.sleep(0.01)
            stream = struct.pack("<hh", 1000, 3000) * 2000
            self._offset = getattr(self, "_offset", 0)
            piece = stream[self._offset % 4:][:4097]
            self._offset += len(piece)
            return piece

    monkeypatch.setattr(subprocess, "Popen", OddReads)
    monkeypatch.setattr(capture.shutil, "which", lambda _name: "/usr/bin/arecord")
    rec = Recorder("plughw:3", max_seconds=5.0)
    try:
        rec.start()
        assert wait_for(lambda: rec._captured_bytes >= 8 * 4097 // 2)
        mono = b"".join(rec._captured)
        assert set(struct.unpack(f"<{len(mono) // 2}h", mono)) == {2000}
    finally:
        rec.close()


def test_stereo_is_mixed_to_one_channel():
    from app.audio import dsp

    frames = struct.pack("<8h", 1000, 0, -200, 200, 32767, 32767, -32768, -32768)
    assert struct.unpack("<4h", dsp.to_mono(frames)) == (500, 0, 32767, -32768)
    assert dsp.to_mono(frames + b"\x01\x02") == dsp.to_mono(frames)   # part frame left


def test_playback_goes_to_the_card_at_48k(monkeypatch):
    """Decoded speech and cues are 8 kHz; the card hears 48 kHz."""
    from app.audio import dsp, playback

    sent = {}

    class FakeAplay:
        returncode = 0

        def __init__(self, command, **_kwargs):
            sent["command"] = command

        def communicate(self, data, timeout=None):
            sent["data"] = data

    monkeypatch.setattr(subprocess, "Popen", FakeAplay)
    monkeypatch.setattr(playback.shutil, "which", lambda _name: "/usr/bin/aplay")
    player = playback.Player("plughw:1")
    assert player.play(bytes(1600)) is True           # 100 ms at 8 kHz
    assert sent["command"][sent["command"].index("-r") + 1] == str(dsp.HARDWARE_RATE)
    assert len(sent["data"]) == 1600 * dsp.FACTOR


def test_clipping_is_measured():
    """Distortion at the microphone is logged: nothing later can undo it."""
    from app.audio.capture import _peak_and_clipping

    clean = struct.pack("<h", 16000) * 1000 + struct.pack("<h", -16000) * 1000
    peak, clipped = _peak_and_clipping(clean)
    assert round(peak) == -6 and clipped == 0.0

    overdriven = struct.pack("<h", 32767) * 100 + struct.pack("<h", 1000) * 900
    _peak, clipped = _peak_and_clipping(overdriven)
    assert clipped == pytest.approx(0.1)
