"""Chats: conversations, typing, quick replies and delivery ticks, end to end.

Drives the app's real input handler (``_on_action``) and the real inbox on
disk; only the radio is a fake that records what would go on the air.
"""

from __future__ import annotations

import time

import pytest
from mfruit_sdk.input import CHAR, ERASE, Action
from PIL import Image, ImageDraw

from app.chat import EVERYONE, QUICK_REPLIES, ChatController
from app.main import WalkieApp
from app.radio import protocol
from app.store import inbox as store
from app.store.inbox import Inbox
from app.ui import navigation, screens, theme
from app.ui.screens import CHAT, CHATS, HOME, REPLIES
from tests.test_pairing import FakeLink
from tests.test_settings_flow import HOLD, QUAD, TAP, THRICE, act, app  # noqa: F401

BASE = 1           # the paired radio in the fixture's roster
ME = 5


class ChatLink(FakeLink):
    def __init__(self):
        super().__init__(ME)
        self.texts, self.acks, self._next = [], [], 40
        self.paired = {BASE, protocol.BROADCAST}

    def can_send(self, addr, type_=protocol.TEXT):
        return addr in self.paired

    def send_text(self, dst, text):
        self._next += 1
        self.texts.append((dst, text, self._next))
        return self._next

    def send_ack(self, dst, msg_id):
        self.acks.append((dst, msg_id))


@pytest.fixture
def radio(app, tmp_path):
    app.inbox = Inbox(tmp_path)
    app.link = ChatLink()
    app.keyring = None
    app.monitor = None
    app._parents = {}
    app.chat = ChatController(app)
    app.state.replies = app.chat.reply_items()
    app._actions = WalkieApp._build_actions(app)
    app._refresh_entries()
    app._refresh_menus()
    return app


def message(type_=protocol.TEXT, body=b"hello", src=BASE, dst=ME, msg_id=7):
    return protocol.Message(type=type_, src=src, msg_id=msg_id, body=body, flags=0,
                            missing=[], rssi_dbm=-80, received_at=time.time(), dst=dst)


def receive(radio, msg):
    item = radio.inbox.add_text(msg, "Base")
    radio.chat.on_text(msg, item)
    return item


def type_text(radio, text):
    for char in text:
        radio._on_action(Action(CHAR, "keyboard", char=char))


def open_base(radio):
    radio.chat.open_list()
    radio.state.chats_index = [row["key"] for row in radio.state.chats].index(BASE)
    act(radio, HOLD)
    assert radio.state.screen == CHAT


# --- finding a conversation ------------------------------------------------------------
def test_home_opens_chats_listing_everyone_then_each_paired_radio(radio):
    radio.state.home_index = [i["key"] for i in radio.state.home_items].index("chats")
    act(radio, HOLD)
    assert radio.state.screen == CHATS
    assert [row["label"] for row in radio.state.chats] == [EVERYONE, "Base", "Back"]
    radio.state.chats_index = 2
    act(radio, HOLD)
    assert radio.state.screen == HOME


def test_a_conversation_opens_on_reply_where_a_hold_talks_to_that_radio(radio):
    open_base(radio)
    assert radio.chat.selected() == "reply"
    assert radio._can_talk()
    assert radio._target == (BASE, "Base")
    act(radio, TAP)                                # to the Back row
    assert radio.state.back_selected and not radio._can_talk()
    act(radio, HOLD)
    assert radio.state.screen == CHATS


# --- typing and ticks -------------------------------------------------------------------------
def test_typing_sends_a_text_that_goes_from_sending_to_sent_to_delivered(radio):
    open_base(radio)
    type_text(radio, "hi tom")
    radio._on_action(Action(ERASE, "keyboard"))
    assert radio.chat.composing and radio.state.compose == "hi to"
    act(radio, HOLD)                               # Enter
    assert not radio.chat.composing
    assert radio.link.texts == [(BASE, "hi to", 41)]
    item = radio.inbox.thread(BASE)[-1]
    assert (item.outgoing, item.status, item.msg_id) == (True, store.SENDING, 41)
    radio.chat.on_sent("text/41", True)
    assert item.status == store.SENT
    radio.chat.on_ack(message(protocol.ACK, body=bytes([41])))
    assert item.status == store.DELIVERED
    assert Inbox(radio.inbox.dir).thread(BASE)[-1].status == store.DELIVERED, "kept on disk"


def test_space_types_while_writing_and_escape_cancels_without_leaving(radio):
    open_base(radio)
    type_text(radio, "a")
    assert radio.chat.composing
    act(radio, QUAD)                               # Esc
    assert radio.state.screen == CHAT and not radio.chat.composing
    assert radio.link.texts == []
    act(radio, QUAD)
    assert radio.state.screen == CHATS


def test_a_dropped_text_is_marked_not_sent(radio):
    open_base(radio)
    type_text(radio, "lost")
    act(radio, HOLD)
    radio.chat.on_sent("text/41", False)
    assert radio.inbox.thread(BASE)[-1].status == store.FAILED


def test_an_interrupted_send_is_failed_after_a_restart(radio):
    radio.inbox.add_sent_text(ME, BASE, "Base", "queued when the power went")
    assert Inbox(radio.inbox.dir).items[0].status == store.FAILED


def test_an_unpaired_radio_is_told_to_pair_and_nothing_is_sent(radio):
    radio.link.paired = set()
    open_base(radio)
    type_text(radio, "hello")
    act(radio, HOLD)
    assert radio.link.texts == [] and "pair with Base" in radio.state.active_banner


# --- quick replies -------------------------------------------------------------------------------
def test_three_clicks_offer_quick_replies_and_hold_sends_one(radio):
    open_base(radio)
    act(radio, THRICE)
    assert radio.state.screen == REPLIES
    act(radio, TAP, HOLD)                          # the second reply
    assert radio.state.screen == CHAT
    assert radio.link.texts == [(BASE, QUICK_REPLIES[1], 41)]


def test_quick_replies_back_row_sends_nothing(radio):
    open_base(radio)
    act(radio, THRICE)
    radio.state.replies_index = len(QUICK_REPLIES)
    act(radio, HOLD)
    assert radio.state.screen == CHAT and radio.link.texts == []


# --- receiving ----------------------------------------------------------------------------------------
def test_a_text_to_us_is_acknowledged_and_a_broadcast_is_not(radio):
    receive(radio, message(msg_id=9))
    assert radio.link.acks == [(BASE, 9)]
    receive(radio, message(dst=protocol.BROADCAST, msg_id=10, body=b"to all"))
    assert radio.link.acks == [(BASE, 9)]
    assert [i.text for i in radio.inbox.thread(protocol.BROADCAST)] == ["to all"]
    assert [i.text for i in radio.inbox.thread(BASE)] == ["hello"]


def test_opening_a_conversation_reads_it_and_new_texts_on_screen_are_read(radio):
    receive(radio, message())
    radio.chat.refresh()
    base = next(row for row in radio.state.chats if row["key"] == BASE)
    assert base["unread"] == 1 and base["value"].startswith("1 new")
    open_base(radio)
    assert radio.inbox.unread == 0
    receive(radio, message(msg_id=8, body=b"again"))
    assert radio.inbox.unread == 0
    assert radio.chat.selected() == "reply", "the selection stays on Reply"


def test_chats_puts_the_latest_conversation_first_after_everyone(radio):
    from app.config.settings import Contact
    from app.store.roster import Roster
    radio.settings.contacts.append(Contact("Hill", 2))
    radio.roster = Roster(radio.settings.contacts, radio.inbox.dir)
    radio.link.paired.add(2)
    receive(radio, message(src=2, body=b"from the hill"))
    radio.chat.refresh()
    assert [row["label"] for row in radio.state.chats][:2] == [EVERYONE, "Hill"]


# --- screens -----------------------------------------------------------------------------------------------
def render(state):
    image = Image.new("RGB", (theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT), theme.BG)
    screens.RENDERERS[state.screen](ImageDraw.Draw(image), state)
    return image


def test_every_chat_screen_renders(radio, tmp_path):
    for n in range(6):
        receive(radio, message(msg_id=n, body=f"message number {n} with a few words".encode()))
    open_base(radio)
    type_text(radio, "x")
    act(radio, QUAD)
    radio.chat.send("a reply that wraps over more than one line of the bubble")
    radio.chat.refresh()
    for name, prepare in (("chat", lambda: None),
                          ("chat-top", lambda: setattr(radio.state, "chat_index", 0)),
                          ("compose", lambda: radio.chat.type_char("t")),
                          ("chats", lambda: radio.chat.open_list()),
                          ("replies", lambda: radio._show(REPLIES))):
        prepare()
        radio.chat.refresh()
        render(radio.state).save(tmp_path / f"{name}.png")
    assert (tmp_path / "chat.png").stat().st_size > 1000


def test_hints_match_the_handlers_on_chat_screens(radio):
    for screen in (CHATS, CHAT, REPLIES):
        for action, (name, _label) in navigation.actions(screen).items():
            assert name in radio._actions or name == navigation.EXIT_APP, (screen, action)
    assert ("hold", "talk") in navigation.hints(CHAT)


# --- voice: sent, arrived, or not confirmed -------------------------------------------------
def voice(src=ME, dst=BASE, msg_id=60):
    return protocol.Message(type=protocol.VOICE, src=src, msg_id=msg_id, body=b"\0" * 32,
                            flags=0, missing=[], rssi_dbm=None, received_at=time.time(),
                            dst=dst, total=1)


def test_a_voice_message_is_ticked_sent_then_delivered_with_an_arrival_message(radio):
    item = radio.inbox.add_voice(voice(), "Base", 3.0, outgoing=True, status=store.SENDING)
    radio.chat.on_sent("voice/60", True)
    assert item.status == store.SENT
    assert "sent · waiting for Base" in radio.state.active_banner
    radio.chat.on_ack(message(protocol.ACK, body=bytes([60])))
    assert item.status == store.DELIVERED
    assert "Base got your 3s voice" in radio.state.active_banner


def test_sent_before_it_was_recorded_still_counts(radio):
    radio.chat.on_sent("voice/61", True)          # the link was quicker than the app
    item = radio.inbox.add_voice(voice(msg_id=61), "Base", 2.0, outgoing=True,
                                 status=store.SENDING)
    radio.chat.track_outgoing(item)
    assert item.status == store.SENT


def test_voice_for_us_is_acknowledged_and_voice_to_everyone_is_not(radio):
    incoming = voice(src=BASE, dst=ME, msg_id=12)
    radio.chat.on_voice(incoming, radio.inbox.add_voice(incoming, "Base", 2.0))
    assert radio.link.acks == [(BASE, 12)]
    everyone = voice(src=BASE, dst=protocol.BROADCAST, msg_id=13)
    radio.chat.on_voice(everyone, radio.inbox.add_voice(everyone, "Base", 2.0))
    assert radio.link.acks == [(BASE, 12)]


def test_an_unconfirmed_message_to_one_radio_says_so_after_a_minute(radio):
    item = radio.inbox.add_voice(voice(), "Base", 3.0, outgoing=True, status=store.SENT)
    assert screens.tick(item) == "✓"
    item.received_at -= 61
    assert screens.tick(item) == "✓ not confirmed"
    item.dst = protocol.BROADCAST
    assert screens.tick(item) == "✓", "nobody confirms a broadcast"


def test_a_dropped_voice_message_says_not_sent(radio):
    item = radio.inbox.add_voice(voice(msg_id=62), "Base", 3.0, outgoing=True,
                                 status=store.SENDING)
    radio.chat.on_sent("voice/62", False)
    assert item.status == store.FAILED and "not sent to Base" in radio.state.active_banner
