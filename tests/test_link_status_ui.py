"""What the operator sees of the link check, and when the checks go out.

The paired list and the Talk screen say whether each radio is in range,
with the signal both ways; a radio dropping out, or coming back, is said
out loud with a banner and a cue. The regular check is one broadcast
ping per interval, held back when the hour's airtime is running short.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config.settings import Contact
from app.radio import protocol
from app.radio.linkcheck import DISCONNECTED, IN_RANGE, LinkMonitor
from app.store.inbox import Inbox
from app.store.keyring import Keyring
from app.store.roster import Entry, Roster
from app.ui import screens
from app.ui.screens import CONTACTS, RANGE, RECORDING, TALK
from tests.test_retrieve import RetrieveLink
from tests.test_screens import display, populated_state  # noqa: F401
from tests.test_settings_flow import app  # noqa: F401

RANGE_VIEW = {"name": "a radio with a very long name", "interval": 30, "air": "9.6k",
              "success": 0.7, "window": (7, 10), "down": -97, "up": -112,
              "sent": 124, "answered": 101, "marks": 12, "elapsed": 3725.0,
              "last_result": "answered in 312 ms",
              "log": "range-20260929-101500.csv"}


# --- the screens ----------------------------------------------------------------
@pytest.mark.parametrize("view,radio_state", [
    ({}, "idle"), (RANGE_VIEW, "idle"), (RANGE_VIEW, RECORDING), (RANGE_VIEW, "sending"),
    (dict(RANGE_VIEW, success=None, down=None, up=None, window=(0, 0),
          last_result="no answer"), "idle"),
])
def test_the_range_test_renders(display, view, radio_state):
    state = populated_state(screen=RANGE, range_view=view, radio_state=radio_state,
                            tx_sent=3, tx_total=12, record_seconds=4.2)
    assert screens.render(display, state) is True


def test_the_range_test_stays_above_the_footer(display):
    from tests.test_screens import _bottom_of_drawn_content

    state = populated_state(screen=RANGE, range_view=RANGE_VIEW)
    image, draw = display.new_canvas()
    screens.draw_range(draw, state)
    assert _bottom_of_drawn_content(image) < screens.CONTENT_BOTTOM + 4


def test_the_paired_list_shows_the_link_check(display):
    state = populated_state(screen=CONTACTS, link_status={
        1: (IN_RANGE, "in range · -72/-80 dBm"),
        2: (DISCONNECTED, "disconnected · 6m ago")})
    assert screens.render(display, state) is True


def test_talk_warns_before_talking_to_a_radio_out_of_range(display):
    state = populated_state(screen=TALK, target_address=1, target_linked=True,
                            link_status={1: (DISCONNECTED, "disconnected · 6m ago")})
    assert screens.render(display, state) is True
    state.link_status = {1: ("keys changed", "keys changed: pair again")}
    assert screens.render(display, state) is True


# --- the app ---------------------------------------------------------------------
class Cues:
    error, tx_done = "error", "done"


class Player:
    def __init__(self):
        self.cues = []

    def cue(self, sound):
        self.cues.append(sound)

    def stop(self):
        pass


@pytest.fixture
def radio(app, tmp_path):
    app.keyring = Keyring(tmp_path / "keys")
    base = Keyring(tmp_path / "base")
    app.keyring.add_peer(1, base.public, base.broadcast_key)
    app.settings.contacts = [Contact("Base", 1)]
    app.roster = Roster(app.settings.contacts, tmp_path)
    app.inbox = Inbox(tmp_path / "inbox")
    app.link = RetrieveLink()
    app.monitor = LinkMonitor(120)
    app.player = Player()
    app.cues = Cues()
    app.board = SimpleNamespace(foreground_ready=True)
    app._parents = {}
    app._fetch_state()
    app._refresh_entries()
    return app


def ping_from_base(radio, rssi=-80, reports=None):
    body = protocol.ping_body(1, 120, reports=reports or {radio.settings.radio.address: -85})
    radio._on_radio_message(
        protocol.Message(type=protocol.PING, src=1, msg_id=1, body=body, flags=0,
                         missing=[], rssi_dbm=rssi, received_at=0.0),
        SimpleNamespace(name="Base"))


def test_a_ping_puts_the_radio_in_range_with_both_signals(radio):
    ping_from_base(radio)
    radio._update_link_status()
    assert radio.state.link_status[1] == (IN_RANGE, "in range · -80/-85 dBm")
    radio._refresh_menus()
    pair = next(i for i in radio.state.home_items if i["key"] == "pair")
    assert "Base in range" in pair["value"]


def test_dropping_out_and_coming_back_are_said_out_loud(radio):
    ping_from_base(radio)
    radio._update_link_status()
    radio.monitor.peers[1].last_heard -= 1000          # a quarter of an hour passes
    radio._update_link_status()
    assert radio.state.link_status[1][0] == DISCONNECTED
    assert "Base disconnected" in radio.state.active_banner
    assert radio.player.cues == ["error"]
    ping_from_base(radio)
    radio._update_link_status()
    assert "Base back in range" in radio.state.active_banner
    assert radio.player.cues == ["error", "done"]


def test_a_radio_whose_packets_cannot_be_opened_needs_pairing_again(radio):
    radio.link.unreadable[1] = 1.0
    radio._update_link_status()
    assert radio.state.link_status[1] == ("keys changed", "keys changed: pair again")


def test_the_regular_check_is_one_broadcast_ping(radio):
    radio._check_due = 0.0
    radio._link_check_tick()
    (dst, ping), = radio.link.pings
    assert dst == protocol.BROADCAST and not ping.reply
    assert ping.interval == radio.settings.radio.link_check_seconds
    radio._link_check_tick()                           # not due again yet
    assert len(radio.link.pings) == 1


def test_no_check_while_the_hour_is_nearly_spent(radio):
    budget = radio.link.budget
    while budget.remaining_seconds() > 0.2 * budget.limit_seconds:
        budget.record(200)
    radio._check_due = 0.0
    radio._link_check_tick()
    assert radio.link.pings == []


def test_no_check_with_nothing_paired(radio):
    radio.keyring.remove_peer(1)
    radio._check_due = 0.0
    radio._link_check_tick()
    assert radio.link.pings == []


def test_opening_talk_on_a_quiet_radio_probes_it(radio):
    radio._talk_to(1, "Base")
    assert radio.link.pings[-1][0] == 1 and radio.link.pings[-1][1].reply


def test_opening_talk_on_a_radio_just_heard_does_not(radio):
    ping_from_base(radio)
    radio._talk_to(1, "Base")
    assert radio.link.pings == []


def test_pings_do_not_light_the_screen(radio):
    pokes = []
    radio.display.poke = lambda: pokes.append(1)
    ping_from_base(radio)
    assert pokes == []


# --- the status bar: how many radios are in range --------------------------------
def ping_from(radio, src, name, rssi=-80):
    body = protocol.ping_body(1, 120, reports={radio.settings.radio.address: -85})
    radio._on_radio_message(
        protocol.Message(type=protocol.PING, src=src, msg_id=1, body=body, flags=0,
                         missing=[], rssi_dbm=rssi, received_at=0.0),
        SimpleNamespace(name=name))


def test_the_status_bar_counts_the_radios_in_range(radio, tmp_path):
    ridge = Keyring(tmp_path / "ridge")
    radio.keyring.add_peer(12, ridge.public, ridge.broadcast_key)
    radio.settings.contacts.append(Contact("Ridge", 12))
    radio.roster = Roster(radio.settings.contacts, tmp_path)
    radio.monitor.watch([1, 12], __import__("time").monotonic())   # what pairing does

    radio._update_link_status()
    assert radio.state.radios_in_range == 0

    ping_from(radio, 1, "Base")
    radio._update_link_status()
    assert radio.state.radios_in_range == 1
    assert radio.state.active_banner == "Base connected"

    ping_from(radio, 12, "Ridge")                       # a second radio connects
    radio._update_link_status()
    assert radio.state.radios_in_range == 2
    assert radio.state.active_banner == "Ridge connected  ·  2 in range"

    radio.monitor.peers[1].last_heard -= 1000          # Base drops out
    radio._update_link_status()
    assert radio.state.radios_in_range == 1
    assert radio.state.active_banner == "Base disconnected  ·  1 in range"


def test_weak_radios_count_but_say_so(radio):
    ping_from(radio, 1, "Base", rssi=-115)
    radio._update_link_status()
    assert radio.state.radios_in_range == 1 and radio.state.radios_weak


def test_a_radio_that_needs_pairing_again_is_not_counted(radio):
    ping_from(radio, 1, "Base")
    radio.link.unreadable[1] = 1.0
    radio._update_link_status()
    assert radio.state.radios_in_range == 0


@pytest.mark.parametrize("count,weak,colour", [
    (0, False, "TEXT_FAINT"), (1, False, "OK"), (2, True, "WARN"),
])
def test_the_count_replaces_the_signal_bars(display, count, weak, colour):
    from app.ui import theme

    state = populated_state(screen=CONTACTS, radios_in_range=count, radios_weak=weak,
                            last_rssi=-60)
    image, draw = display.new_canvas()
    screens.draw_header(draw, state, "Paired")
    bar = image.crop((0, 0, theme.SCREEN_WIDTH, 30))
    pixels = list(bar.getdata())
    assert getattr(theme, colour) in pixels, "the icon and number in their colour"
    if count:
        assert theme.TEXT_FAINT not in pixels or colour == "TEXT_FAINT"
    # The old meter's empty bars were SURFACE_HI; nothing of it is left.
    assert theme.SURFACE_HI not in pixels


# --- a light after each radio's name ----------------------------------------------
@pytest.mark.parametrize("reach,colour,hollow", [
    (IN_RANGE, "OK", False), ("weak signal", "WARN", False),
    (DISCONNECTED, "DANGER", True), ("not checked yet", "TEXT_FAINT", True),
    ("keys changed", "DANGER", False),
])
def test_the_light_follows_the_link_check(reach, colour, hollow):
    from app.ui import theme

    state = populated_state(link_status={1: (reach, "")})
    assert screens.range_mark(state, 1) == {"mark": getattr(theme, colour),
                                            "mark_hollow": hollow}


def test_no_light_for_everyone_or_rows_that_are_not_radios():
    state = populated_state(link_status={1: (IN_RANGE, "")})
    assert screens.range_mark(state, 0xFFFF) == {}
    assert screens.range_mark(state, "back") == {}
    assert screens.range_mark(state, 7) == {}            # not a watched radio


@pytest.mark.parametrize("screen", [TALK, CONTACTS, "chats"])
def test_a_radio_in_range_has_a_green_light_after_its_name(display, screen):
    from app.ui import theme
    from app.ui.screens import CHATS, START

    screen = {TALK: START, "chats": CHATS}.get(screen, screen)
    state = populated_state(screen=screen, link_status={1: (IN_RANGE, "in range")},
                            radios_in_range=0)
    state.start_items = [{"key": "to", "address": 1, "label": "Base", "value": "in range"},
                         {"key": "back", "label": "Back"}]
    state.chats = [{"key": 1, "label": "Base", "value": "voice 4s"},
                   {"key": "back", "label": "Back"}]
    image, draw = display.new_canvas()
    screens.RENDERERS[screen](draw, state)
    # Left part of the rows only: the Paired list prints the signal strength
    # on the right in the same green (seen with DejaVu, when Inter is absent).
    green = [x for x in range(150) for y in range(40, 200)
             if image.getpixel((x, y)) == theme.OK]
    assert green, "a green light on the Base row"
    assert min(green) > 40 and max(green) < 120, "right after the name"
