"""Settings > Paired radios: unpair a radio you no longer talk to."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.chat import ChatController
from app.config.settings import Contact
from app.radio import protocol
from app.store import shared_radio
from app.store.inbox import Inbox
from app.store.keyring import Keyring
from app.store.overrides import Overrides
from app.store.roster import Roster
from app.ui import navigation as nav
from app.ui.screens import CONTACTS, EDIT, SETTINGS, START
from tests.test_pairing import FakeLink
from tests.test_settings_flow import HOLD, QUAD, TAP, act, app, open_setting, play  # noqa: F401


@pytest.fixture
def radio(app, tmp_path):
    """Rover (5) paired with Base (1) and Hill (2)."""
    app.keyring = Keyring(tmp_path / "keys")
    for address in (1, 2):
        other = Keyring(tmp_path / f"k{address}")
        app.keyring.add_peer(address, other.public, other.broadcast_key)
    app.settings.contacts = [Contact("Base", 1), Contact("Hill", 2)]
    for contact in app.settings.contacts:
        app.overrides.add_contact(contact.name, contact.address)
        shared_radio.remember_contact(contact.address, contact.name)
    app.roster = Roster(app.settings.contacts, tmp_path)
    app.link = FakeLink()
    app.board = SimpleNamespace(foreground_ready=True)
    app._parents = {}
    app.monitor = None
    app.inbox = Inbox(tmp_path)
    app.chat = ChatController(app)
    app._refresh_entries()
    app._refresh_menus()
    return app


def open_paired(radio):
    open_setting(radio, "paired")
    assert radio.state.screen == CONTACTS


def test_paired_radios_lists_them_and_a_hold_asks_before_unpairing(radio):
    open_paired(radio)
    assert [e.address for e in radio.state.entries] == [1, 2]
    assert not nav.can_talk(CONTACTS), "a hold here never transmits"
    act(radio, HOLD)
    assert radio.state.screen == EDIT and radio.state.editor_title == "UNPAIR"
    assert radio.keyring.is_paired(1), "nothing before yes"


def test_unpairing_forgets_keys_contact_and_name_everywhere(radio, tmp_path):
    radio._target = (1, "Base")
    open_paired(radio)
    act(radio, HOLD)
    play(radio, TAP, HOLD)                   # onto Yes, then confirm
    assert radio.state.screen == CONTACTS
    assert not radio.keyring.is_paired(1) and radio.keyring.is_paired(2)
    assert [c["address"] for c in Overrides(tmp_path).contacts] == [2]
    assert shared_radio.shared_contacts() == {2: "Hill"}
    assert [e.address for e in radio.state.entries] == [2]
    assert radio._target[0] == protocol.BROADCAST, "no longer the one a hold talks to"
    assert [row.get("address") for row in radio.state.start_items if row["key"] == "to"] == \
        [protocol.BROADCAST, 2]
    assert "unpaired Base" in radio.state.active_banner


def test_cancelling_keeps_the_radio(radio):
    open_paired(radio)
    act(radio, HOLD)
    play(radio, QUAD)
    assert radio.state.screen == CONTACTS and radio.keyring.is_paired(1)


def test_back_row_and_four_clicks_return_to_settings(radio):
    open_paired(radio)
    act(radio, QUAD)
    assert radio.state.screen == SETTINGS
    open_paired(radio)
    act(radio, TAP, TAP, HOLD)               # Base, Hill, Back
    assert radio.state.screen == SETTINGS
