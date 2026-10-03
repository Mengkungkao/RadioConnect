"""The menu the app opens on, and the way back out of everything.

    Home   Talk   > Everyone, each paired radio: moving there chooses it,
                    a hold talks to it, 3x opens its conversation
                  > Replay last voice, Conversations, Back
           Receive                     -> what has come in
           Pair devices                -> see test_pairing
           Settings                    -> name, ID, privacy channel ...

Input goes through the same handler the MFruit OS input controller calls
(`_on_action`), so the tests follow what an operator's presses and keys
actually do: tap next, 2 clicks previous, hold open, 4 clicks back -- and
on talk screens, where a hold talks, 3 clicks opens.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.chat import ChatController
from app.config.settings import Contact
from app.radio import protocol
from app.store.inbox import Inbox
from app.store.keyring import Keyring
from app.store.roster import Roster
from app.ui.screens import CHAT, CHATS, HOME, INBOX, SETTINGS, START, STATUS
from tests.test_pairing import FakeLink
from tests.test_settings_flow import HOLD, QUAD, TAP, THRICE, TWICE, act, app  # noqa: F401


@pytest.fixture
def radio(app, tmp_path):
    """Rover (5) with Base (1) paired, and Hilltop (9) left over from an
    older version: a contact with no keys."""
    app.keyring = Keyring(tmp_path / "keys")
    other = Keyring(tmp_path / "base")
    app.keyring.add_peer(1, other.public, other.broadcast_key)
    app.settings.contacts = [Contact("Base", 1), Contact("Hilltop", 9)]
    app.roster = Roster(app.settings.contacts, tmp_path)
    app.link = FakeLink()
    app.link.can_send = lambda addr, type_=protocol.TEXT: (
        addr == protocol.BROADCAST or app.keyring.is_paired(addr))
    app.board = SimpleNamespace(foreground_ready=True)
    app._parents = {}
    app.inbox = Inbox(tmp_path)
    app.chat = ChatController(app)
    app.state.replies = app.chat.reply_items()
    app._refresh_entries()
    app._refresh_menus()
    return app


press = act


def go_to(app, key):
    """Tap down the current menu to the row `key`, then open it.

    Home opens with a hold; Start is a talk screen, where the hold talks,
    so its rows open with three clicks.
    """
    items = app.state.home_items if app.state.screen == HOME else app.state.start_items
    keys = [item["key"] for item in items]
    index = app.state.home_index if app.state.screen == HOME else app.state.start_index
    opener = HOLD if app.state.screen == HOME else THRICE
    press(app, *[TAP] * ((keys.index(key) - index) % len(keys)), opener)


def test_the_app_opens_on_home(radio):
    assert radio.state.screen == HOME
    assert [i["key"] for i in radio.state.home_items] == \
        ["start", "chats", "receive", "pair", "settings", "status", "range", "back"]


def keys_on_talk(radio):
    return [(row["key"], row.get("address")) for row in radio.state.start_items]


def test_talk_lists_everyone_and_each_paired_radio_then_replay_chats_back(radio):
    go_to(radio, "start")
    assert radio.state.screen == START
    # Hilltop has no keys, so it is not offered: pairing comes first.
    assert keys_on_talk(radio) == [("to", protocol.BROADCAST), ("to", 1),
                                   ("replay", None), ("chats", None), ("back", None)]


def test_moving_to_a_radio_chooses_it_without_opening_anything(radio):
    go_to(radio, "start")
    assert radio._target[0] == protocol.BROADCAST
    press(radio, TAP)                          # to Base
    assert radio.state.screen == START
    assert radio._target == (1, "Base")
    assert radio.state.target_name == "Base"
    assert radio._can_talk()


def test_talk_reopens_on_the_radio_last_talked_to(radio):
    go_to(radio, "start")
    press(radio, TAP, QUAD)                    # Base, then back to Home
    go_to(radio, "start")
    assert radio.state.start_row == "to" and radio._target == (1, "Base")
    assert radio.state.start_items[radio.state.start_index]["address"] == 1


def test_three_clicks_on_a_radio_open_its_conversation(radio):
    go_to(radio, "start")
    press(radio, TAP, THRICE)
    assert radio.state.screen == CHAT and radio.state.chat_peer == (1, "Base")
    press(radio, QUAD)
    assert radio.state.screen == START, "back returns to Talk"


def test_replay_conversations_and_back_rows_open_with_a_hold(radio):
    go_to(radio, "start")
    press(radio, TAP, TAP)                     # from Everyone: Base, then Replay
    assert radio.state.start_row == "replay" and not radio._can_talk()
    press(radio, HOLD)
    assert "no voice yet" in radio.state.active_banner
    press(radio, TAP, HOLD)                    # Conversations
    assert radio.state.screen == CHATS
    press(radio, QUAD)
    press(radio, TAP, HOLD)                    # Back
    assert radio.state.screen == HOME


def test_back_retraces_the_way_in(radio):
    go_to(radio, "start")
    press(radio, QUAD)                         # Talk: four clicks back
    assert radio.state.screen == HOME
    press(radio, QUAD)                         # Home: four clicks leave the app
    assert radio._exit_reason == "user" and not radio.running


def test_receive_opens_from_home_and_goes_back_there(radio):
    go_to(radio, "receive")
    assert radio.state.screen == INBOX
    press(radio, QUAD)
    assert radio.state.screen == HOME


def test_settings_opens_from_home(radio):
    go_to(radio, "settings")
    assert radio.state.screen == SETTINGS
    press(radio, QUAD)
    assert radio.state.screen == HOME


def test_status_opens_only_by_selecting_it(radio):
    press(radio, THRICE)
    assert radio.state.screen == HOME
    go_to(radio, "status")
    assert radio.state.screen == STATUS
    press(radio, TAP)
    assert radio.state.screen == STATUS
    press(radio, HOLD)
    assert radio.state.screen == HOME


def test_lists_step_back_with_two_clicks(radio):
    press(radio, TAP, TAP)
    assert radio.state.home_index == 2
    press(radio, TWICE)
    assert radio.state.home_index == 1


def test_home_says_who_holding_the_button_talks_to(radio):
    go_to(radio, "start")
    press(radio, TAP)                          # Base
    radio._refresh_menus()
    assert "Base" in radio.state.home_items[0]["value"]


def test_home_counts_what_is_new(radio):
    import time
    for n in range(3):
        message = protocol.Message(type=protocol.TEXT, src=1, msg_id=n, body=b"hi", flags=0,
                                   missing=[], rssi_dbm=None, received_at=time.time(), dst=5)
        item = radio.inbox.add_text(message, "Base")
        if n == 0:
            radio.inbox.mark_played(item)
    radio._refresh_menus()
    receive = next(i for i in radio.state.home_items if i["key"] == "receive")
    assert receive["value"].startswith("2 new")


# --- where a hold talks ------------------------------------------------------
class FakeRecorder:
    available = True

    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1
        return True


def hold(radio):
    radio.recorder = FakeRecorder()
    radio.codec = object()
    radio.display.set_led = lambda *_a: None
    radio._on_talk_start()
    return radio.recorder.started


@pytest.mark.parametrize("key", ["receive", "settings"])
def test_a_hold_in_a_menu_or_receive_does_not_talk(radio, key):
    if key == "receive":
        go_to(radio, "receive")
    else:
        go_to(radio, "settings")
    screen = radio.state.screen
    assert hold(radio) == 0
    assert radio.state.screen == screen


def test_a_hold_on_home_says_where_talking_is(radio):
    assert hold(radio) == 0
    assert "Talk" in radio.state.active_banner


def test_receive_says_it_is_for_listening(radio):
    go_to(radio, "receive")
    hold(radio)
    assert "listening" in radio.state.active_banner


def test_a_hold_inside_start_talks_in_place(radio):
    go_to(radio, "start")
    assert hold(radio) == 1
    assert radio.state.screen == START, "the list stays, to replay or leave after"
    assert ("hello", protocol.BROADCAST) not in radio.link.sent
