"""Listen before talk (app.radio.lbt and its use in app.radio.link).

The unit tests drive ChannelSense with a fake clock. The link tests run
whole links on ``fakes.Air``, a channel where packets take airtime and a
packet is lost at a receiver that was transmitting, or heard another
packet, at the same time. Each link test also runs with listen before talk
off, to show that the scenario really collides without it.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.radio import lbt, protocol
from app.radio.lbt import ChannelSense, NoiseFloor
from app.radio.link import LoraLink
from app.radio.sx126x import SX126x
from tests.fakes import Air, AirModule, FakeModule
from tests.test_settings_flow import app  # noqa: F401

FRAGMENT = 0.5
REPLY = 1.0
ME, OTHER, THIRD = 1, 2, 3


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def sense():
    clock = Clock()
    return ChannelSense(FRAGMENT, REPLY, clock=clock), clock


# --- ChannelSense -------------------------------------------------------------
def test_a_clear_channel(sense):
    channel, clock = sense
    until, why = channel.busy()
    assert until <= clock.now and why == ""


def test_a_message_in_progress_holds_until_its_last_fragments(sense):
    channel, clock = sense
    channel.heard(OTHER, protocol.BROADCAST, protocol.VOICE, seq=2, total=4, me=ME)
    until, why = channel.busy()
    assert until == pytest.approx(clock.now + FRAGMENT + lbt.AFTER_MESSAGE_GUARD)
    assert "2 is sending (3 of 4)" == why
    # Never more than AHEAD_FRAGMENTS ahead, re-armed by each fragment.
    channel.heard(OTHER, protocol.BROADCAST, protocol.VOICE, seq=0, total=40, me=ME)
    until, _ = channel.busy()
    assert until == pytest.approx(clock.now + lbt.AHEAD_FRAGMENTS * FRAGMENT
                                  + lbt.AFTER_MESSAGE_GUARD)
    clock.now = until
    assert channel.busy()[0] <= clock.now


def test_a_request_to_someone_else_leaves_room_for_the_answer(sense):
    channel, clock = sense
    channel.heard(OTHER, THIRD, protocol.TEXT, seq=0, total=1, me=ME)
    assert channel.busy()[0] == pytest.approx(clock.now + REPLY)
    # An answer to someone else, or a request to us, only gets the short guard:
    # for a request to us, we are the one answering.
    for dst, type_ in ((THIRD, protocol.ACK), (ME, protocol.TEXT),
                       (protocol.BROADCAST, protocol.TEXT)):
        channel.heard(OTHER, dst, type_, seq=0, total=1, me=ME)
        assert channel.busy()[0] == pytest.approx(clock.now + lbt.AFTER_MESSAGE_GUARD)


def test_after_our_request_only_the_answer_may_go(sense):
    channel, clock = sense
    channel.transmitted(0.3)
    channel.expect_answer(OTHER)
    until, why = channel.busy()
    assert until == pytest.approx(clock.now + 0.3 + REPLY)
    assert "waiting for 2 to answer" == why
    # An answer of ours waits only for our own packet to leave the air.
    assert channel.busy(answering=True)[0] == pytest.approx(clock.now + 0.3)
    clock.now += 0.4
    channel.heard(OTHER, ME, protocol.PONG, seq=0, total=1, me=ME)
    assert channel.busy()[0] == pytest.approx(clock.now + lbt.AFTER_MESSAGE_GUARD)


def test_bytes_arriving_mean_busy(sense):
    channel, clock = sense
    channel.bytes_arrived()
    assert channel.busy() == (pytest.approx(clock.now + lbt.BYTE_GUARD), "receiving")


def test_noise_floor():
    noise = NoiseFloor(margin_db=10, strong_dbm=-80)
    assert not noise.busy(-108)            # the first reading only sets the floor
    assert not noise.busy(-104)
    assert noise.busy(-97)                 # 11 dB above the quietest
    assert noise.busy(-60)                 # strong whatever the floor
    fresh = NoiseFloor()
    assert fresh.busy(-70)


# --- the link on a channel with airtime and collisions -------------------------
def make_link(module, addr, monkeypatch, **kwargs):
    monkeypatch.setattr("serial.Serial", lambda *a, **k: module)
    radio = SX126x(port="fake", addr=addr, freq_mhz=868, mode_pins=None)
    return LoraLink(radio, duty_cycle_percent=100.0, callsign=f"r{addr}", **kwargs)


class Inbox:
    def __init__(self):
        self.messages = []
        self.changed = threading.Condition()

    def __call__(self, message, peer):
        with self.changed:
            self.messages.append(message)
            self.changed.notify_all()

    def wait_for(self, predicate, timeout=10.0):
        deadline = time.monotonic() + timeout
        with self.changed:
            while not predicate(self.messages):
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.changed.wait(left)
        return True


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True


def voice_complete(messages):
    return any(m.type == protocol.VOICE and m.complete and not m.missing for m in messages)


def run_ping_then_voice(monkeypatch, listen: bool):
    """The 2026-10-10 Orange Pi log: a ping asking for an answer, then a
    voice message straight after it. The answer (pong) went out while the
    voice was on the air, and its first fragments were lost."""
    monkeypatch.setattr(protocol, "QUIET_SECONDS", 0.6)
    air = Air()
    left, right = AirModule.network(air, 2)
    alice = make_link(left, 1, monkeypatch, listen_before_talk=listen)
    bob = make_link(right, 2, monkeypatch, listen_before_talk=listen)
    alice._remember = lambda *_args: None        # nothing kept to repair from
    alice_inbox, bob_inbox = Inbox(), Inbox()
    alice.on_message(alice_inbox)
    bob.on_message(bob_inbox)
    alice.start()
    bob.start()
    try:
        alice.send_ping(2, protocol.ping_body(1, reply=True))
        alice.send_voice(2, bytes(range(200)) * 3, codec_mode=8)   # four fragments
        bob_inbox.wait_for(lambda ms: any(m.type == protocol.VOICE for m in ms), 15.0)
        alice_inbox.wait_for(lambda ms: any(m.type == protocol.PONG for m in ms), 3.0)
        return alice_inbox.messages, bob_inbox.messages, air.lost
    finally:
        alice.stop()
        bob.stop()


def test_the_answer_to_a_ping_does_not_collide_with_the_next_message(monkeypatch):
    alice_got, bob_got, lost = run_ping_then_voice(monkeypatch, listen=True)
    assert voice_complete(bob_got), f"lost on the air: {lost}"
    assert any(m.type == protocol.PONG for m in alice_got)
    assert lost == []


def test_without_listening_the_ping_answer_collides(monkeypatch):
    """Negative control for the test above."""
    alice_got, bob_got, lost = run_ping_then_voice(monkeypatch, listen=False)
    assert lost, "the scenario must collide when nobody listens"
    assert not voice_complete(bob_got) or not any(m.type == protocol.PONG for m in alice_got)


def run_third_radio_talks_over(monkeypatch, listen: bool, noise: bool, when: str):
    """Alice sends Bob a four-fragment voice message; Carol sends Bob a text
    while it is on the air: after hearing its first fragment ("heard"), or
    while that first fragment is still in flight ("in flight")."""
    monkeypatch.setattr(protocol, "QUIET_SECONDS", 0.6)
    air = Air()
    a, b, c = AirModule.network(air, 3, reports_noise=noise)
    alice = make_link(a, 1, monkeypatch, listen_before_talk=listen)
    bob = make_link(b, 2, monkeypatch, listen_before_talk=listen)
    carol = make_link(c, 3, monkeypatch, listen_before_talk=listen, noise_check=noise)
    alice._remember = lambda *_args: None
    inbox = Inbox()
    bob.on_message(inbox)
    for link in (alice, bob, carol):
        link.start()
    try:
        if noise and listen:
            carol._probe_noise()                       # what its first message would do
        alice.send_voice(2, bytes(range(200)) * 3, codec_mode=8)
        if when == "heard":
            # Fragment 1 on the air, fragment 0 already heard.
            assert wait_until(lambda: carol.stats.overheard >= 1 and air.transmitting(a))
        else:
            assert wait_until(lambda: air.transmitting(a))
        carol.send_text(2, "over")
        inbox.wait_for(lambda ms: voice_complete(ms)
                       and any(m.type == protocol.TEXT for m in ms), 15.0)
        return inbox.messages, air.lost, carol
    finally:
        for link in (alice, bob, carol):
            link.stop()


@pytest.mark.parametrize("listen", [True, False])
def test_a_third_radio_waits_for_a_message_it_hears(monkeypatch, listen):
    messages, lost, carol = run_third_radio_talks_over(monkeypatch, listen, False, "heard")
    if listen:
        assert voice_complete(messages) and lost == []
        assert any(m.type == protocol.TEXT for m in messages)
        assert carol.stats.lbt_waits == 1
    else:                                              # negative control
        assert lost


@pytest.mark.parametrize("listen", [True, False])
def test_the_channel_level_catches_a_first_fragment_in_flight(monkeypatch, listen):
    messages, lost, carol = run_third_radio_talks_over(monkeypatch, listen, True, "in flight")
    if listen:
        assert carol.noise_supported is True
        assert voice_complete(messages) and lost == []
        assert any(m.type == protocol.TEXT for m in messages)
    else:                                              # negative control
        assert lost


def test_a_module_that_does_not_report_the_level_is_asked_once(monkeypatch):
    """A module that does not know the query may send it as a few bytes on a
    wrong channel, so it is asked once, and the answer is handed back to be
    remembered; a module known not to answer is never asked."""
    air = Air()
    a, b = AirModule.network(air, 2, reports_noise=False)
    alice = make_link(a, 1, monkeypatch, noise_check=True)
    learnt = []
    alice.on_noise_probed = learnt.append
    inbox = Inbox()
    bob = make_link(b, 2, monkeypatch)
    bob.on_message(inbox)
    alice.start()
    bob.start()
    try:
        for word in ("one", "two", "three"):
            alice.send_text(protocol.BROADCAST, word)
        assert inbox.wait_for(lambda ms: len(ms) == 3)
        assert alice.noise_supported is False and learnt == [False]
        assert a.noise_queries == 1
    finally:
        alice.stop()
        bob.stop()

    a2, b2 = AirModule.network(Air(), 2, reports_noise=False)
    known = make_link(a2, 1, monkeypatch, noise_check=True, noise_known=False)
    inbox = Inbox()
    receiver = make_link(b2, 2, monkeypatch)
    receiver.on_message(inbox)
    known.start()
    receiver.start()
    try:
        known.send_text(protocol.BROADCAST, "four")
        assert inbox.wait_for(lambda ms: len(ms) == 1)
        assert a2.noise_queries == 0
    finally:
        known.stop()
        receiver.stop()


def test_a_module_known_to_report_the_level_is_not_probed(monkeypatch):
    a, b = AirModule.network(Air(), 2, reports_noise=True)
    alice = make_link(a, 1, monkeypatch, noise_check=True, noise_known=True)
    learnt = []
    alice.on_noise_probed = learnt.append
    inbox = Inbox()
    bob = make_link(b, 2, monkeypatch)
    bob.on_message(inbox)
    alice.start()
    bob.start()
    try:
        alice.send_text(protocol.BROADCAST, "one")
        assert inbox.wait_for(lambda ms: len(ms) == 1)
        assert learnt == [] and a.noise_queries == 1     # the check before sending
        assert alice.stats.noise_dbm == -110
    finally:
        alice.stop()
        bob.stop()


def test_the_answer_is_remembered_per_radio_setup(tmp_path):
    from app.store.overrides import Overrides
    store = Overrides(tmp_path)
    assert store.module_reports_level("2026-10-05T23:38:03+1100") is None
    store.remember_module_reports_level(False, "2026-10-05T23:38:03+1100")
    again = Overrides(tmp_path)
    assert again.module_reports_level("2026-10-05T23:38:03+1100") is False
    assert again.module_reports_level("2026-10-11T09:00:00+1100") is None, \
        "set up again: ask again"


def test_the_level_answer_is_taken_out_of_the_byte_stream(monkeypatch):
    """Split across reads, before a real packet: the packet still arrives,
    and the answer reaches neither the deframer nor the signal meter (0xC1
    would read as -63 dBm)."""
    left, right = FakeModule.pair(rssi_byte=None)
    bob = make_link(right, 2, monkeypatch)
    inbox = Inbox()
    bob.on_message(inbox)
    bob._noise_wanted = True
    bob.start()
    try:
        right.inject(bytes([0xC1]))
        right.inject(bytes([0x00, 0x01, 256 - 97]))
        assert bob._noise_answered.wait(2.0)
        assert bob._noise_reading == -97
        alice = make_link(left, 1, monkeypatch)
        alice.start()
        try:
            alice.send_text(protocol.BROADCAST, "after")
            assert inbox.wait_for(lambda ms: len(ms) == 1)
        finally:
            alice.stop()
        assert inbox.messages[0].body == b"after"
        assert bob.stats.last_rssi is None
        assert bob.stats.frames_dropped == 0
    finally:
        bob.stop()


def test_listen_before_talk_can_be_turned_off(monkeypatch):
    left, _right = FakeModule.pair()
    link = make_link(left, 1, monkeypatch, listen_before_talk=False, noise_check=True)
    assert link.sense is None and link.noise_supported is False
def test_the_app_remembers_on_its_main_loop(app):  # noqa: F811
    """Learnt on the transmit thread, written by the main loop."""
    app.settings.radio.provisioned_at = "2026-10-05T23:38:03+1100"
    app._module_level_learnt(False)
    assert app.overrides.module_reports_level("2026-10-05T23:38:03+1100") is None
    app._remember_module_level()
    assert app.overrides.module_reports_level("2026-10-05T23:38:03+1100") is False
