"""A microphone another program holds, and a stream that stops feeding.

Orange Pi Zero 2W, 2026-10-22: a recorder the AI Chatbot left behind kept
the Whisplay card, so RadioConnect's `arecord` quit at once with "Device or
resource busy". The recorder still counted as warm, every press recorded
0 bytes, and the only thing the operator heard was "too short" -- even
after the card was free again.

The recorder tests run a real stand-in `arecord` on PATH: busy while a
flag file exists, a steady stream otherwise.
"""

from __future__ import annotations

import sys
import time

import pytest

from app.audio.capture import Recorder
from app.ui.screens import IDLE, RECORDING, START
from tests.test_menu import go_to, radio  # noqa: F401
from tests.test_settings_flow import app  # noqa: F401

BUSY = "arecord: main:831: audio open error: Device or resource busy"


def wait_for(condition, timeout=5.0):
    """Wait, bounded, for state the recorder's thread reports."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def card(tmp_path, monkeypatch):
    """The stand-in arecord; `card.busy` is the flag file."""
    script = tmp_path / "bin" / "arecord"
    script.parent.mkdir()
    stream = ("import sys, time\n"
              "chunk = (4000).to_bytes(2, 'little', signed=True) * 3072\n"
              "while True:\n"
              "    sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush(); time.sleep(0.02)\n")
    script.write_text(
        "#!/bin/sh\n"
        f'if [ -e "{tmp_path}/busy" ]; then echo "{BUSY}" >&2; exit 1; fi\n'
        f'exec "{sys.executable}" -u -c "{stream}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{script.parent}:/usr/bin:/bin")
    card = type("Card", (), {})()
    card.busy = tmp_path / "busy"
    yield card


@pytest.fixture
def recorder(card):
    rec = Recorder("plughw:3", max_seconds=5.0)
    yield rec
    rec.close()


def test_a_busy_card_is_reported_in_arecords_words(card, recorder):
    card.busy.touch()
    recorder.arm()
    assert wait_for(lambda: recorder.error), "the dead stream must say why"
    assert recorder.error == BUSY
    assert recorder.busy


def test_a_press_on_a_busy_card_fails_while_it_is_held(card, recorder):
    card.busy.touch()
    recorder.arm()
    assert wait_for(lambda: recorder.error)
    assert recorder.start() is True          # opens the card again: still busy
    assert wait_for(lambda: recorder.failed)
    assert recorder.stop() == b""


def test_the_next_press_records_once_the_card_is_free_again(card, recorder):
    """The regression: a dead stream was trusted as warm for good."""
    card.busy.touch()
    recorder.arm()
    assert wait_for(lambda: recorder._process.poll() is not None)
    card.busy.unlink()                       # the other program let go
    assert recorder.start() is True
    assert wait_for(lambda: recorder._captured_bytes > 0), "the press kept nothing"
    assert len(recorder.stop()) > 0
    assert recorder.error is None and recorder.armed


def test_an_armed_recorder_still_records_after_a_press_hits_the_cap(card):
    """The cap used to end the stream, leaving an armed recorder deaf."""
    rec = Recorder("plughw:3", max_seconds=0.2, preroll_seconds=0.1)
    try:
        rec.arm()
        rec.start()
        assert wait_for(lambda: rec._captured_bytes >= int(0.2 * 96000))
        rec.stop()
        rec.start()
        assert wait_for(lambda: rec._captured_bytes > 0), "the next press kept nothing"
        assert len(rec.stop()) > 0
    finally:
        rec.close()


# --- what the operator is told ---------------------------------------------
class BusyRecorder:
    """The card is held elsewhere: the stream dies as soon as it opens."""

    available = True
    level = 0.0
    elapsed = 0.0
    last_clipped = 0.0
    error = BUSY
    busy = True

    def __init__(self):
        self.recording = False

    @property
    def failed(self):
        return self.recording

    def start(self):
        self.recording = True
        return True

    def stop(self):
        self.recording = False
        return b""


@pytest.fixture
def talking(radio):
    go_to(radio, "start")
    assert radio.state.screen == START
    radio.recorder = BusyRecorder()
    radio.codec = object()
    radio.display.set_led = lambda *_a: None
    radio._on_talk_start()
    assert radio.state.radio_state == RECORDING
    return radio


def test_a_held_press_on_a_busy_microphone_says_so_at_once(talking):
    talking._check_the_microphone()
    assert talking.state.radio_state == IDLE, "the press ends: nothing is being kept"
    assert "busy" in talking.state.active_banner
    talking._on_talk_end(2.0)                # the release that follows
    assert "too short" not in talking.state.active_banner


def test_a_quick_press_on_a_busy_microphone_does_not_say_too_short(talking):
    talking._on_talk_end(0.5)                # released before a frame noticed
    assert "busy" in talking.state.active_banner
    assert "too short" not in talking.state.active_banner
