"""A long voice message waits for airtime instead of being thrown away, and
a floating pill says whether a message is pending, sending or sent."""

from __future__ import annotations

import time

from PIL import Image, ImageDraw

from app.radio import protocol
from app.radio.airtime import AirtimeBudget
from app.radio.link import MAX_AIRTIME_WAIT, TxStatus
from app.ui import screens, theme
from app.ui.screens import CHAT, SENDING, START, ViewState
from tests.test_chats import BASE, radio  # noqa: F401
from tests.test_settings_flow import app  # noqa: F401


def test_the_budget_says_when_a_message_will_fit():
    budget = AirtimeBudget(2400, duty_cycle_percent=1.0)      # 36 s an hour
    assert budget.wait_for(10) == 0
    budget.record(5000)                                         # most of the hour's airtime
    assert 0 < budget.wait_for(30) <= 3600
    assert budget.wait_for(budget.limit_seconds + 1) == budget.window, "never fits"


def test_pending_then_sending_then_nothing(radio):
    now = time.monotonic()
    radio.link.pending = lambda: 0
    radio.link.sending = TxStatus("voice/12", BASE, 0, 20, now + 75)
    assert radio._outbox_text() == "pending: voice to Base · airtime in 1:15"
    radio.link.sending = TxStatus("voice/12", BASE, 3, 20)
    assert radio._outbox_text() == "sending voice to Base · 3/20"
    radio.link.sending = TxStatus("ping", protocol.BROADCAST, 0, 1)
    assert radio._outbox_text() == "", "bookkeeping is not shown"
    radio.link.sending = None
    assert radio._outbox_text() == ""


def test_a_message_waiting_for_the_channel_says_so(radio):
    """Listen before talk: held for another radio, not for the hour's airtime."""
    now = time.monotonic()
    radio.link.pending = lambda: 0
    radio.link.sending = TxStatus("text/4", BASE, 0, 1, now + 3, why="channel")
    assert radio._outbox_text() == "pending: text to Base · channel busy"
    radio.link.pending = lambda: 2
    radio.link.sending = TxStatus("ack/9", BASE, 0, 1, now + 3, why="channel")
    assert radio._outbox_text() == "pending: 2 waiting for a clear channel"


def test_the_pill_floats_over_talk_and_conversations():
    for screen in (START, CHAT):
        state = ViewState(screen=screen, outbox="pending: voice to Base · airtime in 0:42",
                          start_items=[{"key": "back", "label": "Back"}])
        image = Image.new("RGB", (theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT), theme.BG)
        screens.RENDERERS[screen](ImageDraw.Draw(image), state)
        assert image.getbbox() is not None


def test_the_talking_disc_says_pending_while_it_waits_for_airtime():
    state = ViewState(screen=START, radio_state=SENDING, tx_total=20,
                      outbox="pending: voice to Base · airtime in 0:42")
    image = Image.new("RGB", (theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT), theme.BG)
    screens.draw_talk(ImageDraw.Draw(image), state)        # draws without error


def test_waiting_is_capped_at_ten_minutes():
    assert MAX_AIRTIME_WAIT == 600.0


def test_received_bubbles_say_who_sent_them(radio):
    message = protocol.Message(type=protocol.VOICE, src=BASE, msg_id=3, body=b"\0" * 16,
                               flags=0, missing=[], rssi_dbm=None, received_at=time.time(),
                               dst=protocol.BROADCAST, total=1)
    item = radio.inbox.add_voice(message, "Base", 4.0)
    _text, meta, _colour = screens._bubble_text(item)
    assert meta.startswith("Base · ")
    sent = radio.inbox.add_sent_text(5, BASE, "Base", "hi")
    assert not screens._bubble_text(sent)[1].startswith("Base")


def test_a_long_voice_message_is_queued_as_pending_not_refused(radio):
    """Reported: over 7 s said "duty cycle full" and nothing was sent."""
    sent = []
    radio.codec = type("Codec", (), {"encode": lambda self, pcm: b"\0" * 3200})()
    radio.codec_mode = 0
    radio.range_test = None
    radio.display.set_led = lambda *_a: None
    radio.link.send_voice = lambda dst, encoded, mode: sent.append(dst) or 99
    radio._target = (BASE, "Base")
    budget = radio.link.budget
    while budget.remaining_seconds() > 1:
        budget.record(200)                  # the hour's airtime is spent for now...
    budget._events = type(budget._events)(  # ...but most of it 59 minutes ago
        (stamp - 3540, seconds) for stamp, seconds in budget._events)
    assert radio.link.budget.wait_for(radio.link.budget.estimate_message(3500)) > 0
    radio._encode_and_send(b"\0" * 32000, 8.0)
    assert sent == [BASE]
    assert "pending" in radio.state.active_banner
